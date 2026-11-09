import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { TaskScope } from '@society0/host/tasks';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { ActivationIntent, ActivationAdmission, ActivationResult, ExecutionContext, Json } from '../contracts/runtime.js';
import type { ActivationPolicy, WakeDecision, WakeLedger, WakeSignal } from '../contracts/activation-policy.js';
import { compareCodePoints } from '@society0/host/order';
import { slotKey } from '@society0/host/slots';
import { metricsOf } from '../contracts/metrics.js';
import { activationKey } from '../activation-mailbox/index.js';
import { registerActionNotifier } from './notifier.js';
import '../drivers/index.js';
import '../actor-eligibility/index.js';
export const Config = Schema.object({
    capacity: Schema.natural().min(1).default(4), maxActivations: Schema.natural().min(1).default(1024), maxQueuedBytes: Schema.natural().min(1).default(1048576),
    admission: Schema.object({
        kind: Schema.union(['count', 'kv']).default('count'),
        replicas: Schema.natural().min(1).default(2),
        tokensPerReplica: Schema.natural().min(1).default(288000),
        utilization: Schema.number().min(0).max(1).default(0.6),
        defaultTokens: Schema.natural().min(1).default(8192),
        maxTokens: Schema.natural().min(0).default(512)
    }).default({})
});
export const metadata: PluginMetadata = {
    title: 'activation', summary: '主体串行邮箱与真实任务排空', configVersion: 1, configPolicy: ['capacity', 'maxActivations', 'maxQueuedBytes', 'admission'].map(k => ({
        path: '/' + k, mode: 'new-run', affectsResults: true
    })), scope: 'generation', hostAbi: 'society0/host/1'
};
export const api: ApiEntry = {
    exportPath: './activation', provides: [{
            key: 'activation', protocol: 'activation', major: 1, capabilities: []
        }], requires: ['drivers', 'actors', 'tasks', 'activationPolicy', 'actorEligibility', 'database', 'executionFence', 'slots'].map(key => ({
        service: {
            key, protocol: key, major: 1, capabilities: []
        }, optional: false, phase: 'module'
    }))
};
export const wakePolicySlot = slotKey<ActivationPolicy>('society/activation-policy');
export interface WakeOutcome {
    readonly signal: WakeSignal;
    readonly decision: WakeDecision;
    readonly admission?: ActivationAdmission;
}
interface Item {
    intent: ActivationIntent;
    signals: {
        id: string;
        source: string;
        input: Json;
    }[];
    inputs: Json[];
    bytes: number;
    tokens: number;
    key: string;
    order: number;
}
interface Batch {
    context: ExecutionContext;
    queue: Item[];
    active: Set<string>;
    jobs: Set<Promise<void>>;
    rounds: Map<string, number>;
    identities: Map<string, string>;
    intentIds: Map<string, string>;
    results: {
        order: number;
        result: ActivationResult;
    }[];
    bytes: number;
    count: number;
    submitted: number;
    admission: Promise<void>;
    failure?: unknown;
    cancel: AbortController;
}
declare module '@society0/host' {
    interface Context {
        activation: ActivationService;
    }
}
export class ActivationService extends Service {
    static Config = Config;
    static inject = ['drivers', 'actors', 'tasks', 'activationPolicy', 'actorEligibility', 'database', 'executionFence', 'slots'];
    private batches = new Map<object, Batch>();
    private released = new WeakSet<object>();
    private inboxes = new Map<string, WakeSignal[]>();
    private wakes = new Map<string, number>();
    private pairs = new Map<string, number>();
    private wakeSequence = 0;
    private inflightTokens = 0;
    private tasks: TaskScope;
    private closing = false;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) {
        super(ctx, 'activation');
        this.config = Config(input);
        this.tasks = ctx.tasks.bind();
        ctx.slots.declare({
            key: wakePolicySlot, strategy: 'keyed', required: false, validate: (value: unknown) => {
                if (typeof (value as ActivationPolicy)?.decide !== 'function')
                    throw Object.assign(new Error('ACTIVATION_POLICY_INVALID'), { code: 'ACTIVATION_POLICY_INVALID' });
                return value as ActivationPolicy;
            }
        });
        ctx.effect(() => registerActionNotifier(ctx, this), 'activation.action-notify');
        ctx.effect(() => async () => {
            this.closing = true;
            for (const b of this.batches.values())
                b.cancel.abort(Error('ACTIVATION_CLOSING'));
            await Promise.allSettled([...this.batches.values()].map(b => this.settle(b)));
            await this.tasks.drain('activation closing');
        });
    }
    private batch(context: ExecutionContext) {
        if (this.released.has(context.lease))
            throw Error('ACTIVATION_CONTEXT_CLOSED');
        let b = this.batches.get(context.lease);
        if (!b) {
            b = {
                context, queue: [], active: new Set(), jobs: new Set(), rounds: new Map(), identities: new Map(), intentIds: new Map(), results: [], bytes: 0, count: 0, submitted: 0, admission: Promise.resolve(), cancel: new AbortController()
            };
            this.batches.set(context.lease, b);
        }
        return b;
    }
    async submit(intent: ActivationIntent, context: ExecutionContext): Promise<ActivationAdmission> {
        const b = this.batch(context);
        const encoded = JSON.stringify(intent);
        if (Buffer.byteLength(encoded) > this.config.maxQueuedBytes)
            return {
                kind: 'rejected', reason: 'ACTIVATION_BYTE_LIMIT'
            };
        intent = JSON.parse(encoded) as ActivationIntent;
        let result!: ActivationAdmission;
        const next = b.admission.then(async () => {
            result = await this.admit(b, intent);
        });
        b.admission = next.catch(() => {
        });
        await next;
        if (result.kind !== 'rejected') {
            const metrics = metricsOf(this.ctx);
            metrics?.count('activations.total');
            metrics?.count('activations.bySource.' + intent.source);
        }
        this.pump(b);
        return result;
    }
    private async eligible(b: Batch, intent: ActivationIntent) {
        const view = await b.context.database.snapshot({
            maxAgeMs: 30000
        });
        try {
            return await this.ctx.actorEligibility.evaluate({
                kind: 'activation', actorId: intent.actorId, momentId: intent.momentId, logicalTime: b.context.logicalTime, intentId: intent.id, driver: intent.driver
            }, view);
        }
        finally {
            await view.close();
        }
    }
    private admissionClosed(batch: Batch) {
        return this.closing || batch.cancel.signal.aborted || batch.context.signal.aborted || !!batch.failure;
    }
    private identityConflict(batch: Batch, intent: ActivationIntent, key: string, identity: string): ActivationAdmission | undefined {
        const previous = batch.intentIds.get(intent.id);
        if (previous && previous !== identity)
            return {
                kind: 'rejected', reason: 'ACTIVATION_ID_CONFLICT'
            };
        if (previous)
            return {
                kind: 'rejected', reason: 'ACTIVATION_DUPLICATE'
            };
        const driver = batch.identities.get(key);
        if (driver && driver !== intent.driver)
            return {
                kind: 'rejected', reason: 'ACTIVATION_IDENTITY_CONFLICT'
            };
    }
    private budgetConflict(batch: Batch, key: string, queued: Item | undefined, bytes: number): ActivationAdmission | undefined {
        if (batch.bytes + bytes > this.config.maxQueuedBytes)
            return {
                kind: 'rejected', reason: 'ACTIVATION_BYTE_LIMIT'
            };
        if (!queued && (batch.rounds.get(key) ?? 0) >= 2)
            return {
                kind: 'rejected', reason: 'ACTIVATION_FOLLOWUP_LIMIT'
            };
        if (!queued && batch.count + batch.queue.length >= this.config.maxActivations)
            return {
                kind: 'rejected', reason: 'ACTIVATION_BUDGET'
            };
    }
    private async admit(batch: Batch, intent: ActivationIntent): Promise<ActivationAdmission> {
        if (this.admissionClosed(batch))
            return {
                kind: 'rejected', reason: 'ACTIVATION_CLOSED'
            };
        const decision = await this.eligible(batch, intent);
        const tokens = await this.estimateTokens(intent);
        if (this.admissionClosed(batch))
            return {
                kind: 'rejected', reason: 'ACTIVATION_CLOSED'
            };
        if (!decision.allowed)
            return {
                kind: 'rejected', reason: JSON.stringify(decision)
            };
        const identity = JSON.stringify([intent.actorId, intent.momentId, intent.driver]), key = activationKey(intent);
        const conflict = this.identityConflict(batch, intent, key, identity);
        if (conflict)
            return conflict;
        const queued = batch.queue.find(item => item.key === key);
        const policy = this.ctx.activationPolicy.admit(intent, queued ? [queued.intent] : []);
        if (policy.kind === 'rejected')
            return policy;
        const bytes = Buffer.byteLength(JSON.stringify(intent)), budget = this.budgetConflict(batch, key, queued, bytes);
        if (budget)
            return budget;
        batch.identities.set(key, intent.driver);
        batch.intentIds.set(intent.id, identity);
        batch.bytes += bytes;
        return this.enqueue(batch, intent, key, bytes, queued, tokens);
    }
    private tokenBudget() { return Math.floor(this.config.admission.replicas * this.config.admission.tokensPerReplica * this.config.admission.utilization); }
    private inputTokens(intent: ActivationIntent) { return Math.max(0, Math.ceil(Buffer.byteLength(JSON.stringify(intent.input ?? null)) / 3.2)); }
    /** 只读线程最近一条 assistant usage；不打开全量历史。 */
    private async lastPromptTokens(intent: ActivationIntent): Promise<number | undefined> {
        let view;
        try {
            view = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
            const [row] = await view.query({
                sql: `SELECT tm.payload AS payload FROM thread_messages tm JOIN threads t ON t.id=tm.thread_id
WHERE t.actor_id=? AND t.moment_id IN (?,?) AND tm.role='assistant' ORDER BY tm.seq DESC LIMIT 1`,
                parameters: [intent.actorId, intent.momentId, 'thread/actor/' + intent.actorId]
            }, { maxRows: 1, maxBytes: 1048576 });
            if (!row) return undefined;
            const stored = JSON.parse(String(row.payload));
            const message = stored?.kind === 'inline' ? stored.value : undefined;
            const input = Number(message?.usage?.input);
            return Number.isFinite(input) && input >= 0 ? input : undefined;
        }
        catch { return undefined; }
        finally { await view?.close(); }
    }
    private async estimateTokens(intent: ActivationIntent): Promise<number> {
        if (this.config.admission.kind !== 'kv') return 0;
        const delta = this.inputTokens(intent), previous = await this.lastPromptTokens(intent);
        if (previous !== undefined) return Math.max(1, previous + delta + this.config.admission.maxTokens);
        if (typeof intent.estimatedTokens === 'number' && Number.isFinite(intent.estimatedTokens) && intent.estimatedTokens > 0) return Math.ceil(intent.estimatedTokens);
        return Math.max(1, Math.min(this.config.admission.defaultTokens, delta));
    }
    private enqueue(batch: Batch, intent: ActivationIntent, key: string, bytes: number, queued: Item | undefined, tokens: number): ActivationAdmission {
        const signal = {
            id: intent.id, source: intent.source, input: intent.input
        };
        if (queued) {
            queued.inputs.push(intent.input);
            queued.signals.push(signal);
            queued.bytes += bytes;
            queued.tokens = Math.max(queued.tokens, tokens);
            return {
                kind: 'coalesced', into: queued.intent.id, mergedInput: structuredClone(queued.inputs)
            };
        }
        batch.queue.push({
            intent, signals: [signal], inputs: [intent.input], bytes, tokens, key, order: batch.submitted++
        });
        return {
            kind: 'accepted', key
        };
    }
    private async policy(): Promise<ActivationPolicy | undefined> {
        const selection = await this.ctx.slots.acquire(wakePolicySlot, { generation: 'read' });
        const item = [...selection.items].sort((left, right) => compareCodePoints(left.id, right.id))[0];
        // 选择保留到本次调用结束；槽在唤醒期间不释放会让 release 与调用方耦合，因此立即复制引用。
        const value = item?.value;
        await selection.release();
        return value;
    }
    private wakeKey(actor: string, moment: string) { return moment + '\u0000' + actor; }
    private pairKey(from: string, actor: string, moment: string) {
        const [left, right] = compareCodePoints(from, actor) <= 0 ? [from, actor] : [actor, from];
        return moment + '\u0000' + left + '\u0000' + right;
    }
    private recordWake(signal: WakeSignal) {
        const key = this.wakeKey(signal.actor, signal.moment);
        this.wakes.set(key, (this.wakes.get(key) ?? 0) + 1);
        if (signal.from) {
            const pair = this.pairKey(signal.from, signal.actor, signal.moment);
            this.pairs.set(pair, (this.pairs.get(pair) ?? 0) + 1);
        }
    }
    inbox(actor: string, moment?: string): readonly WakeSignal[] {
        if (moment)
            return [...(this.inboxes.get(this.wakeKey(actor, moment)) ?? [])];
        const values: WakeSignal[] = [];
        for (const [key, items] of this.inboxes)
            if (key.endsWith('\u0000' + actor))
                values.push(...items);
        return values;
    }
    inboxCount(moment?: string): number {
        if (!moment)
            return [...this.inboxes.values()].reduce((total, items) => total + items.length, 0);
        let total = 0;
        for (const [key, items] of this.inboxes)
            if (key.startsWith(moment + '\u0000'))
                total += items.length;
        return total;
    }
    /**
     * 按 ActivationPolicy 处理唤醒：wake 入队，notify 写入本 tick 收件箱，drop 丢弃。
     * 每 tick 每主体与每对主体的计数只统计真正 wake 的信号。
     */
    async wake(signals: readonly WakeSignal[], context: ExecutionContext): Promise<readonly WakeOutcome[]> {
        const policy = await this.policy();
        const ledger: WakeLedger = {
            wakeCount: (actor, moment) => this.wakes.get(this.wakeKey(actor, moment)) ?? 0,
            pairTurns: (from, actor, moment) => this.pairs.get(this.pairKey(from, actor, moment)) ?? 0,
        };
        const outcomes: WakeOutcome[] = [];
        for (const signal of signals) {
            if (this.admissionClosed(this.batch(context)))
                break;
            const decision = policy ? policy.decide(signal, ledger) : 'wake';
            if (decision === 'wake') {
                this.recordWake(signal);
                const intent: ActivationIntent = {
                    id: signal.id ?? [signal.kind, signal.actor, signal.moment, signal.source, String(++this.wakeSequence)].join('/'),
                    actorId: signal.actor,
                    momentId: signal.moment,
                    source: signal.source,
                    driver: signal.driver ?? 'llm/v1',
                    input: signal.input ?? { kind: signal.kind, source: signal.source, ...(signal.from !== undefined ? { from: signal.from } : {}) },
                };
                outcomes.push({ signal, decision, admission: await this.submit(intent, context) });
                continue;
            }
            if (decision === 'notify') {
                const key = this.wakeKey(signal.actor, signal.moment);
                const items = this.inboxes.get(key) ?? [];
                items.push(signal);
                this.inboxes.set(key, items);
            }
            outcomes.push({ signal, decision });
        }
        return outcomes;
    }
    private pump(b: Batch) {
        while (!b.failure && !b.cancel.signal.aborted && !b.context.signal.aborted && b.active.size < this.config.capacity) {
            const kv = this.config.admission.kind === 'kv', budget = this.tokenBudget();
            const eligible = b.queue.map((item, index) => ({ item, index })).filter(({ item }) => !b.active.has(item.intent.actorId) && (!kv || b.active.size === 0 || this.inflightTokens + item.tokens <= budget));
            const chosen = eligible.find(({ item }) => item.intent.priority !== 'background') ?? eligible[0];
            const index = chosen?.index ?? -1;
            if (index < 0)
                break;
            const item = b.queue.splice(index, 1)[0]!;
            b.bytes -= item.bytes;
            b.active.add(item.intent.actorId);
            this.inflightTokens += item.tokens;
            b.count++;
            b.rounds.set(item.key, (b.rounds.get(item.key) ?? 0) + 1);
            const job = this.execute(b, item).catch(error => {
                b.failure ??= error;
                this.ctx.executionFence.halt(String(b.failure));
                b.cancel.abort(error);
            }).finally(() => {
                this.inflightTokens -= item.tokens;
                b.active.delete(item.intent.actorId);
                b.jobs.delete(job);
                this.pump(b);
            });
            b.jobs.add(job);
        }
    }
    private async execute(b: Batch, item: Item) {
        const decision = await this.eligible(b, item.intent);
        if (!decision.allowed) {
            b.results.push({
                order: item.order, result: this.decorate({
                    status: 'incomplete', value: null, evidence: {
                        kind: 'eligibility-rejected', decision: decision as unknown as Json
                    }
                }, item.intent)
            });
            return;
        }
        const signal = AbortSignal.any([b.context.signal, b.cancel.signal]);
        const intent = {
            ...item.intent, signals: item.signals, input: item.inputs.length === 1 ? item.inputs[0]! : item.inputs
        };
        const baseOperationId = JSON.stringify(['activation', b.context.operationId, intent.actorId, intent.momentId, intent.driver, intent.id]);
        const attempt = Math.max(0, Math.trunc(b.context.recoveryAttempt ?? 0));
        const operationId = attempt > 0 ? `${baseOperationId}/r${attempt}` : baseOperationId;
        const recovery = attempt > 0 ? { attempt, baseOperationId } : undefined;
        const task = this.tasks.run('activation:' + intent.id, async (taskSignal) => {
            const selection = await this.ctx.drivers.acquire(intent.driver, {
                ...b.context, operationId, signal: taskSignal
            });
            try {
                return await selection.value.activate(intent, {
                    ...b.context, operationId, signal: taskSignal, writeLease: b.context.lease, actorId: intent.actorId, momentId: intent.momentId, ...(recovery ? { recovery } : {})
                });
            }
            finally {
                await selection.release();
            }
        }, {
            signal, bytes: item.bytes
        });
        try {
            const result = await task;
            b.results.push({
                order: item.order, result: this.decorate(result, item.intent)
            });
        }
        finally {
            await task.settled;
        }
    }
    private decorate(result: ActivationResult, intent: ActivationIntent): ActivationResult {
        return { ...result, activationId: intent.id, actorId: intent.actorId, momentId: intent.momentId };
    }
    private async settle(b: Batch) {
        while (true) {
            const admission = b.admission;
            await admission;
            this.pump(b);
            if (b.jobs.size)
                await Promise.all([...b.jobs]);
            if (b.admission !== admission || b.jobs.size)
                continue;
            break;
        }
        if (b.queue.length) {
            for (const item of b.queue)
                b.results.push({
                    order: item.order, result: this.decorate({
                        status: 'incomplete', value: item.inputs, evidence: {
                            reason: 'cancelled-before-dispatch'
                        }
                    }, item.intent)
                });
            b.queue = [];
            b.bytes = 0;
        }
        if (b.failure)
            throw b.failure;
    }
    async drain(context: ExecutionContext) {
        const b = this.batch(context);
        await this.settle(b);
        return b.results.sort((a, c) => a.order - c.order).map(x => x.result);
    }
    release(context: ExecutionContext) {
        const b = this.batches.get(context.lease);
        if (b?.jobs.size || b?.queue.length)
            throw Error('ACTIVATION_UNSETTLED');
        this.batches.delete(context.lease);
        this.released.add(context.lease);
    }
    async cancel(context: ExecutionContext, reason: string) {
        const b = this.batch(context);
        if (b.jobs.size)
            this.ctx.executionFence.halt(reason);
        b.cancel.abort(Error(reason));
        await this.settle(b);
    }
}
export default ActivationService;
