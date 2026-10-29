import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { TaskScope } from '@society0/host/tasks';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { ActivationIntent, ActivationAdmission, ActivationResult, ExecutionContext, Json } from '../contracts/runtime.js';
import { activationKey } from '../activation-mailbox/index.js';
import '../drivers/index.js';
import '../actor-eligibility/index.js';
export const Config = Schema.object({
    capacity: Schema.natural().min(1).default(4), maxActivations: Schema.natural().min(1).default(1024), maxQueuedBytes: Schema.natural().min(1).default(1048576)
});
export const metadata: PluginMetadata = {
    title: 'activation', summary: '主体串行邮箱与真实任务排空', configVersion: 1, configPolicy: ['capacity', 'maxActivations', 'maxQueuedBytes'].map(k => ({
        path: '/' + k, mode: 'new-run', affectsResults: true
    })), scope: 'generation', hostAbi: 'society0/host/1'
};
export const api: ApiEntry = {
    exportPath: './activation', provides: [{
            key: 'activation', protocol: 'activation', major: 1, capabilities: []
        }], requires: ['drivers', 'actors', 'tasks', 'activationPolicy', 'actorEligibility', 'database', 'executionFence'].map(key => ({
        service: {
            key, protocol: key, major: 1, capabilities: []
        }, optional: false, phase: 'module'
    }))
};
interface Item {
    intent: ActivationIntent;
    signals: {
        id: string;
        source: string;
        input: Json;
    }[];
    inputs: Json[];
    bytes: number;
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
    static inject = ['drivers', 'actors', 'tasks', 'activationPolicy', 'actorEligibility', 'database', 'executionFence'];
    private batches = new Map<object, Batch>();
    private released = new WeakSet<object>();
    private tasks: TaskScope;
    private closing = false;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) {
        super(ctx, 'activation');
        this.config = Config(input);
        this.tasks = ctx.tasks.bind();
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
        return this.enqueue(batch, intent, key, bytes, queued);
    }
    private enqueue(batch: Batch, intent: ActivationIntent, key: string, bytes: number, queued: Item | undefined): ActivationAdmission {
        const signal = {
            id: intent.id, source: intent.source, input: intent.input
        };
        if (queued) {
            queued.inputs.push(intent.input);
            queued.signals.push(signal);
            queued.bytes += bytes;
            return {
                kind: 'coalesced', into: queued.intent.id, mergedInput: structuredClone(queued.inputs)
            };
        }
        batch.queue.push({
            intent, signals: [signal], inputs: [intent.input], bytes, key, order: batch.submitted++
        });
        return {
            kind: 'accepted', key
        };
    }
    private pump(b: Batch) {
        while (!b.failure && !b.cancel.signal.aborted && !b.context.signal.aborted && b.active.size < this.config.capacity) {
            const index = b.queue.findIndex(i => !b.active.has(i.intent.actorId));
            if (index < 0)
                break;
            const item = b.queue.splice(index, 1)[0]!;
            b.bytes -= item.bytes;
            b.active.add(item.intent.actorId);
            b.count++;
            b.rounds.set(item.key, (b.rounds.get(item.key) ?? 0) + 1);
            const job = this.execute(b, item).catch(error => {
                b.failure ??= error;
                this.ctx.executionFence.halt(String(b.failure));
                b.cancel.abort(error);
            }).finally(() => {
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
                order: item.order, result: {
                    status: 'incomplete', value: null, evidence: {
                        kind: 'eligibility-rejected', decision: decision as unknown as Json
                    }
                }
            });
            return;
        }
        const signal = AbortSignal.any([b.context.signal, b.cancel.signal]);
        const intent = {
            ...item.intent, signals: item.signals, input: item.inputs.length === 1 ? item.inputs[0]! : item.inputs
        };
        const operationId = JSON.stringify(['activation', b.context.operationId, intent.actorId, intent.momentId, intent.driver, intent.id]);
        const task = this.tasks.run('activation:' + intent.id, async (taskSignal) => {
            const selection = await this.ctx.drivers.acquire(intent.driver, {
                ...b.context, operationId, signal: taskSignal
            });
            try {
                return await selection.value.activate(intent, {
                    ...b.context, operationId, signal: taskSignal, writeLease: b.context.lease, actorId: intent.actorId, momentId: intent.momentId
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
                order: item.order, result
            });
        }
        finally {
            await task.settled;
        }
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
                    order: item.order, result: {
                        status: 'incomplete', value: item.inputs, evidence: {
                            reason: 'cancelled-before-dispatch'
                        }
                    }
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
