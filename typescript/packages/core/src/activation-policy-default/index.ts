import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { ActivationPolicy, WakeDecision, WakeLedger, WakeSignal } from '../contracts/activation-policy.js';
import { wakePolicySlot } from '../activation/index.js';

export const Config = Schema.object({
    maxWakesPerTick: Schema.natural().min(1).default(3),
    maxTurnsPerPair: Schema.natural().min(1).default(4),
    decisionEvery: Schema.natural().min(1).default(1),
    schedule: Schema.union([Schema.const('barrier'), Schema.const('pipeline')]).default('barrier'),
});

export const metadata: PluginMetadata = {
    title: 'activation-policy-default',
    summary: '默认唤醒策略：广播/公开通知不唤醒，私信与需回应回执唤醒，并按 tick 与主体对设上限。',
    configVersion: 1,
    configPolicy: [
        { path: '/maxWakesPerTick', mode: 'new-run', affectsResults: true, secret: false },
        { path: '/maxTurnsPerPair', mode: 'new-run', affectsResults: true, secret: false },
        { path: '/decisionEvery', mode: 'new-run', affectsResults: true, secret: false },
        { path: '/schedule', mode: 'new-run', affectsResults: true, secret: false },
    ],
    scope: 'generation',
    hostAbi: 'society0/host/1',
};

export const api: ApiEntry = {
    exportPath: './activation-policy-default',
    provides: [{ key: 'wakePolicy', protocol: 'wakePolicy', major: 1, capabilities: [] }],
    requires: [{ service: { key: 'slots', protocol: 'slots', major: 1, capabilities: [] }, optional: false, phase: 'module' }],
};

declare module '@society0/host' {
    interface Context {
        wakePolicy: ActivationPolicyDefault;
    }
}

export interface ActivationPolicyMetrics {
    decisions: number;
    wakes: number;
    notifies: number;
    drops: number;
    degraded: number;
}

const tick = (moment: string): bigint | undefined => /^\d+$/.test(moment) ? BigInt(moment) : undefined;

export class ActivationPolicyDefault extends Service implements ActivationPolicy {
    static Config = Config;
    static inject = ['slots'];
    private config: ReturnType<typeof Config>;
    readonly metrics: ActivationPolicyMetrics = { decisions: 0, wakes: 0, notifies: 0, drops: 0, degraded: 0 };

    constructor(ctx: Context, input = {}) {
        super(ctx, 'wakePolicy');
        this.config = Config(input);
        ctx.slots.contribute(wakePolicySlot, { id: 'default', value: this });
    }

    private base(signal: WakeSignal): WakeDecision {
        switch (signal.kind) {
            case 'broadcast':
            case 'public':
                return 'notify';
            case 'dm':
            case 'consent':
            case 'agenda':
                return 'wake';
            case 'receipt':
                return signal.requiresResponse ? 'wake' : 'notify';
            case 'clock': {
                const value = tick(signal.moment);
                if (value === undefined)
                    return 'wake';
                return value % BigInt(this.config.decisionEvery) === 0n ? 'wake' : 'notify';
            }
            default:
                return 'notify';
        }
    }

    decide(signal: WakeSignal, ledger: WakeLedger): WakeDecision {
        this.metrics.decisions++;
        let decision = this.base(signal);
        if (decision === 'wake') {
            const overActor = ledger.wakeCount(signal.actor, signal.moment) >= this.config.maxWakesPerTick;
            const overPair = signal.from !== undefined && ledger.pairTurns(signal.from, signal.actor, signal.moment) >= this.config.maxTurnsPerPair;
            if (overActor || overPair) {
                decision = 'notify';
                this.metrics.degraded++;
            }
        }
        if (decision === 'wake')
            this.metrics.wakes++;
        else if (decision === 'notify')
            this.metrics.notifies++;
        else
            this.metrics.drops++;
        return decision;
    }
}

export default ActivationPolicyDefault;
