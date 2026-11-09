import { RuntimeWork, activationStatus } from '../runtime-staged/lifecycle.js';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "runtime-staged", "summary": "runtime-staged", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./runtime-staged", "provides": [{
            "key": "runtimeDriver", "protocol": "runtimeDriver", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "activation", "protocol": "activation", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import type { ActivationIntent, ActivationResult, AdvanceResult, ExecutionContext, Proposal, RuntimeDriver as Runtime, Json } from '../contracts/runtime.js';
import '../activation/index.js';
import { integer } from '../clock-ticks/index.js';
export interface StagedPlan {
    readonly phases: readonly {
        readonly id: string;
        readonly activations: readonly ActivationIntent[];
    }[];
    readonly nextCursor: string;
}
declare module '@society0/host' {
    interface Context {
        runtimeDriver: Runtime;
    }
}
export class RuntimeDriver extends Service implements Runtime {
    static Config = Config;
    static inject = ['activation'];
    readonly planKinds = ['staged/1'];
    private work: RuntimeWork;
    constructor(ctx: Context) {
        super(ctx, 'runtimeDriver');
        this.work = new RuntimeWork(ctx.activation);
    }
    execute(proposal: Proposal, context: ExecutionContext): Promise<AdvanceResult> {
        return this.work.track(context, this.advance(proposal, context));
    }
    private plan(proposal: Proposal) {
        if (proposal.planKind !== 'staged/1')
            throw Error('RUNTIME_PLAN_KIND');
        const plan = proposal.payload as unknown as StagedPlan;
        if (!Array.isArray(plan?.phases) || typeof plan.nextCursor !== 'string')
            throw Error('STAGED_PLAN');
        if (integer(plan.nextCursor) <= integer(proposal.cursor))
            throw Error('STAGED_CURSOR');
        return plan;
    }
    private async submitPhase(phase: StagedPlan['phases'][number], context: ExecutionContext, admissions: Json[], intents: ActivationIntent[]) {
        context.signal.throwIfAborted();
        if (!Array.isArray(phase.activations))
            throw Error('STAGED_PHASE');
        let rejected = false;
        for (const intent of phase.activations) {
            intents.push(intent);
            const result = await this.ctx.activation.submit(intent, context);
            admissions.push({
                ...result
            });
            if (result.kind === 'rejected')
                rejected = true;
        }
        await this.ctx.activation.drain(context);
        return rejected;
    }
    private async advance(proposal: Proposal, context: ExecutionContext): Promise<AdvanceResult> {
        const plan = this.plan(proposal), admissions: Json[] = [], intents: ActivationIntent[] = [];
        let rejected = false;
        try {
            for (const phase of plan.phases)
                rejected = await this.submitPhase(phase, context, admissions, intents) || rejected;
            const results = await this.ctx.activation.drain(context);
            return {
                status: activationStatus(rejected, results), frontier: {
                    codec: 'staged/1', value: plan.nextCursor
                }, requiredOperations: results.flatMap(result => result.requiredOperations ?? []), evidence: {
                    admissions, activations: results.map((result, index) => ({ ...(intents[index] ? { id: intents[index]!.id, actorId: intents[index]!.actorId } : {}), ...result })) as unknown as Json
                }
            };
        }
        catch (error) {
            await this.ctx.activation.cancel(context, 'runtime failed').catch(() => {
            });
            throw error;
        }
    }
    async drain(reason: string) {
        await this.work.drain(reason);
    }
}
export default RuntimeDriver;
