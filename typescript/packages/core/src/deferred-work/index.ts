/** C8 延后任务通道：complete 事务登记、恢复重入队、后台执行与按命名空间等待。 */
import { compareCodePoints } from '@society0/host/order';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import { randomUUID } from 'node:crypto';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { TaskScope } from '@society0/host/tasks';
import type { CommitReceipt, SqliteTransaction, TransactionRequest, SqliteRow } from '../sqlite/index.js';
import type { Json } from '../contracts/common.js';
import type { ActivationAdmission, ActivationIntent, ExecutionContext } from '../contracts/runtime.js';
import type { DeferredContext, DeferredHandler, DeferredJob, DeferredJobRecord, DeferredProduce, DeferredRunResult, DeferredWork } from '../contracts/deferred-work.js';
import { deferredJobId, deferredWorkMigration } from '../contracts/deferred-work.js';
import { fault } from '../sqlite/contracts.js';
import { metricsOf } from '../contracts/metrics.js';
import '../sqlite/index.js';
import '../execution-fence/index.js';
import '@society0/host/tasks';
export { deferredJobId, deferredWorkMigration } from '../contracts/deferred-work.js';
export type { DeferredContext, DeferredHandler, DeferredJob, DeferredJobRecord, DeferredProduce, DeferredRunResult, DeferredWork } from '../contracts/deferred-work.js';

export const Config = Schema.object({
    concurrency: Schema.natural().min(1).default(2),
    maxAttempts: Schema.natural().min(1).default(3),
    maxTransactionMs: Schema.natural().min(1).default(30000),
    maxJobBytes: Schema.natural().min(1).default(65536)
});
export const metadata: PluginMetadata = {
    title: 'deferred-work',
    summary: 'Off-critical-path jobs registered in the complete transaction and resumed by content hash',
    configVersion: 1,
    configPolicy: ['concurrency', 'maxAttempts', 'maxTransactionMs', 'maxJobBytes'].map(key => ({ path: '/' + key, mode: 'new-run' as const, affectsResults: true })),
    scope: 'run',
    allowedScopes: ['run'],
    hostAbi: 'society0/host/1'
};
export const api: ApiEntry = {
    exportPath: './deferred-work',
    provides: [{ key: 'deferredWork', protocol: 'deferredWork', major: 1, capabilities: [] }],
    requires: ['database', 'executionFence', 'tasks'].map(key => ({
        service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module'
    }))
};
declare module '@society0/host' {
    interface Context {
        deferredWork: DeferredWork;
    }
}
function tick(value: string | number): bigint {
    const text = String(value);
    if (!/^(0|[1-9][0-9]*)$/.test(text)) fault('DEFERRED_TICK', text);
    return BigInt(text);
}
function tickOrUndefined(value: string): bigint | undefined {
    return /^(0|[1-9][0-9]*)$/.test(value) ? BigInt(value) : undefined;
}
function delay(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, ms);
        const abort = () => { clearTimeout(timer); reject(signal.reason); };
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort();
    });
}
function record(row: SqliteRow): DeferredJobRecord {
    return {
        id: String(row.id), kind: String(row.kind), inputs: JSON.parse(String(row.inputs)),
        produces: String(row.produces), maxLagTicks: Number(row.max_lag_ticks),
        enqueuedTick: String(row.enqueued_tick),
        state: row.state === 'running' ? 'queued' : String(row.state) as DeferredJobRecord['state'],
        ...(row.produced_at === null ? {} : { producedAt: String(row.produced_at) }),
        ...(row.output === null ? {} : { output: JSON.parse(String(row.output)) }),
        ...(row.error === null ? {} : { error: String(row.error) }),
        attempts: Number(row.attempts)
    };
}
function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
}

export class DeferredWorkService extends Service implements DeferredWork {
    static Config = Config;
    static inject = ['database', 'executionFence', 'tasks'];
    private options: ReturnType<typeof Config>;
    private tasks!: TaskScope;
    private pendingJobs: DeferredJob[] = [];
    private records = new Map<string, DeferredJobRecord>();
    private handlers = new Map<string, DeferredHandler>();
    private active = new Map<string, Promise<void>>();
    private waiters: { ns: string; uptoTick: bigint; resolve: () => void; reject: (error: unknown) => void }[] = [];
    private closing = false;
    constructor(ctx: Context, input: Partial<ReturnType<typeof Config>> = {}) {
        super(ctx, 'deferredWork');
        this.options = Config(input);
    }
    async [Service.init]() {
        this.tasks = this.ctx.tasks.bind();
        this.ctx.effect(() => () => { this.closing = true; });
        await this.load();
        this.publish('');
    }
    private async load() {
        const view = await this.ctx.database.snapshot({ maxAgeMs: this.options.maxTransactionMs });
        try {
            const rows = await view.query({
                sql: "SELECT * FROM deferred_jobs WHERE state IN ('queued','running','failed') ORDER BY enqueued_tick,id"
            }, { maxRows: 10000, maxBytes: 1048576 });
            for (const row of rows) this.records.set(String(row.id), record(row));
        }
        finally { await view.close(); }
    }
    enqueue(job: DeferredJob): string {
        if (!job || typeof job.kind !== 'string' || !job.kind) fault('DEFERRED_JOB', 'kind');
        if (typeof job.produces !== 'string' || !job.produces) fault('DEFERRED_JOB', 'produces');
        if (!Number.isSafeInteger(job.maxLagTicks) || job.maxLagTicks < 0) fault('DEFERRED_JOB', 'maxLagTicks');
        if (job.priority !== undefined && job.priority !== 'background') fault('DEFERRED_JOB', 'priority');
        const id = job.id ?? deferredJobId(job);
        if (this.records.has(id) || this.pendingJobs.some(item => (item.id ?? deferredJobId(item)) === id)) return id;
        this.pendingJobs.push({ ...job, id });
        return id;
    }
    async commit(tx: SqliteTransaction, currentTick: string): Promise<void> {
        if (!this.pendingJobs.length) { this.publish(currentTick); return; }
        const jobs = this.pendingJobs;
        this.pendingJobs = [];
        for (const job of jobs) {
            const id = job.id!;
            if (this.records.has(id)) continue;
            await tx.execute({
                sql: 'INSERT INTO deferred_jobs(id,kind,inputs,produces,max_lag_ticks,enqueued_tick,state,attempts) VALUES(?,?,?,?,?,?,\'queued\',0) ON CONFLICT(id) DO NOTHING',
                parameters: [id, job.kind, JSON.stringify(job.inputs ?? null), job.produces, job.maxLagTicks, currentTick]
            });
            this.records.set(id, { ...job, id, enqueuedTick: currentTick, state: 'queued', attempts: 0 });
        }
        this.publish(currentTick);
    }
    begin(): void { this.pendingJobs = []; }
    discard(): void { this.pendingJobs = []; }
    pending(): readonly DeferredJob[] { return [...this.pendingJobs]; }
    read(id: string): DeferredJobRecord | undefined {
        const found = this.records.get(id);
        return found ? { ...found } : undefined;
    }
    failures(): readonly DeferredJobRecord[] {
        return [...this.records.values()].filter(job => job.state === 'failed').map(job => ({ ...job }));
    }
    describe() {
        const values = [...this.records.values()];
        return {
            queued: values.filter(item => item.state === 'queued').length,
            running: values.filter(item => item.state === 'running').length,
            completed: values.filter(item => item.state === 'done').length,
            failed: values.filter(item => item.state === 'failed').length
        };
    }
    register(kind: string, handler: DeferredHandler): () => void {
        if (!kind || !handler || typeof handler.run !== 'function') fault('DEFERRED_HANDLER', kind);
        this.handlers.set(kind, handler);
        this.wake();
        return () => { if (this.handlers.get(kind) === handler) this.handlers.delete(kind); };
    }
    background(intent: ActivationIntent, context: ExecutionContext): Promise<ActivationAdmission> {
        const activation = this.ctx.get('activation', false) as { submit(intent: ActivationIntent, context: ExecutionContext): Promise<ActivationAdmission> } | undefined;
        if (!activation) fault('DEFERRED_ACTIVATION');
        return activation!.submit({ ...intent, priority: 'background', source: 'deferred:' + intent.source }, context);
    }
    wake(): void {
        if (this.closing) return;
        const ready = [...this.records.values()]
            .filter(job => job.state === 'queued' && !this.active.has(job.id) && this.handlers.has(job.kind) && job.attempts < this.options.maxAttempts)
            .sort((left, right) => {
                const a = tickOrUndefined(left.enqueuedTick), b = tickOrUndefined(right.enqueuedTick);
                if (a !== undefined && b !== undefined && a !== b) return a < b ? -1 : 1;
                return compareCodePoints(left.id, right.id);
            });
        for (const job of ready) {
            if (this.active.size >= this.options.concurrency) break;
            this.start(job);
        }
    }
    private start(job: DeferredJobRecord): void {
        const running: DeferredJobRecord = { ...job, state: 'running', attempts: job.attempts + 1 };
        this.records.set(job.id, running);
        const bytes = Math.min(this.options.maxJobBytes, Buffer.byteLength(JSON.stringify({ id: job.id, kind: job.kind, inputs: job.inputs, produces: job.produces })));
        const task = this.tasks.run('deferred:' + job.kind + ':' + job.id, signal => this.run(running, signal), { bytes });
        const settled = task.then(() => {}, () => {}).finally(() => {
            this.active.delete(job.id);
            this.pump();
        });
        this.active.set(job.id, settled);
        void settled.catch(() => {});
    }
    private pump(): void {
        if (this.closing) return;
        this.wake();
        this.checkWaiters();
    }
    private async acquire(signal: AbortSignal): Promise<Awaited<ReturnType<Context['executionFence']['reserve']>>> {
        while (true) {
            signal.throwIfAborted();
            try {
                return await this.ctx.executionFence.reserve({ id: 'deferred/' + randomUUID() });
            }
            catch (error) {
                if (!/FENCE_BUSY/.test(errorMessage(error))) throw error;
                await delay(20, signal);
            }
        }
    }
    private async transaction<T>(signal: AbortSignal, operationId: string, body: (tx: SqliteTransaction) => Promise<T>, request: Partial<TransactionRequest> = {}): Promise<{ value: T; receipt: CommitReceipt }> {
        const reservation = await this.acquire(signal);
        try {
            const budget: TransactionRequest = {
                maxDurationMs: this.options.maxTransactionMs,
                maxStatements: 64,
                maxInputBytes: 1048576,
                maxChangedRows: 64,
                maxCapturedBytes: 4194304,
                ...request,
                operationId
            };
            return await this.ctx.database.transaction(reservation.lease, budget, body);
        }
        finally { await reservation.release(); }
    }
    private async run(job: DeferredJobRecord, signal: AbortSignal): Promise<void> {
        const handler = this.handlers.get(job.kind);
        if (!handler) return;
        const producedAt = job.enqueuedTick;
        const context: DeferredContext = {
            operationId: 'deferred:' + job.id,
            producedAt,
            logicalTime: job.enqueuedTick,
            runId: this.ctx.database.runId,
            signal,
            database: this.ctx.database,
            transaction: (operationId, body, request) => this.transaction(signal, operationId, body, request)
        };
        let result: DeferredRunResult;
        try {
            result = await handler.run(job, context);
        }
        catch (error) {
            if (signal.aborted || this.closing) this.records.set(job.id, { ...job, state: 'queued', attempts: job.attempts });
            else await this.failed(job, error, signal);
            return;
        }
        const produce = result && typeof result === 'object' && !Array.isArray(result) && typeof (result as DeferredProduce).write === 'function'
            ? result as DeferredProduce : undefined;
        const output = produce ? produce.output : result as Json | undefined;
        try {
            await this.transaction(signal, 'deferred-complete-' + job.id, async tx => {
                if (produce) await produce.write(tx, job, context);
                await tx.execute({
                    sql: "UPDATE deferred_jobs SET state='done',produced_at=?,output=?,error=NULL,attempts=? WHERE id=?",
                    parameters: [producedAt, output === undefined ? null : JSON.stringify(output), job.attempts, job.id]
                });
            });
            this.records.set(job.id, { ...job, state: 'done', producedAt, ...(output === undefined ? {} : { output }) });
        }
        catch (error) {
            if (signal.aborted || this.closing) this.records.set(job.id, { ...job, state: 'queued', attempts: job.attempts });
            else await this.failed(job, error, signal);
        }
    }
    private async failed(job: DeferredJobRecord, error: unknown, signal: AbortSignal): Promise<void> {
        const message = errorMessage(error);
        this.records.set(job.id, { ...job, state: 'failed', error: message });
        const metrics = metricsOf(this.ctx);
        metrics?.gauge('deferred.failed', this.failures().length);
        metrics?.gauge('deferred.queued', [...this.records.values()].filter(item => item.state === 'queued' || item.state === 'running').length);
        try {
            await this.transaction(signal, 'deferred-failed-' + job.id + '-' + job.attempts, async tx => {
                await tx.execute({
                    sql: "UPDATE deferred_jobs SET state='failed',error=?,attempts=? WHERE id=?",
                    parameters: [message, job.attempts, job.id]
                });
            });
        }
        catch { /* 失败记录也是观测；不改变原始错误。 */ }
    }
    async await(ns: string, uptoTick: string | number): Promise<void> {
        if (!ns) fault('DEFERRED_NAMESPACE');
        const target = tick(uptoTick);
        const blocked = [...this.records.values()].filter(job => job.produces === ns && job.state !== 'done' && (tickOrUndefined(job.enqueuedTick) ?? 0n) < target);
        const failed = blocked.find(job => job.state === 'failed');
        if (failed) throw new Error(failed.error ?? 'DEFERRED_JOB_FAILED');
        if (!blocked.length) return;
        return new Promise<void>((resolve, reject) => this.waiters.push({ ns, uptoTick: target, resolve, reject }));
    }
    private checkWaiters(): void {
        for (const waiter of [...this.waiters]) {
            const blocked = [...this.records.values()].filter(job => job.produces === waiter.ns && job.state !== 'done' && (tickOrUndefined(job.enqueuedTick) ?? 0n) < waiter.uptoTick);
            const failed = blocked.find(job => job.state === 'failed');
            if (failed) {
                this.waiters = this.waiters.filter(item => item !== waiter);
                waiter.reject(new Error(failed.error ?? 'DEFERRED_JOB_FAILED'));
            }
            else if (!blocked.length) {
                this.waiters = this.waiters.filter(item => item !== waiter);
                waiter.resolve();
            }
        }
    }
    private publish(currentTick: string): void {
        const metrics = metricsOf(this.ctx);
        if (!metrics) return;
        const values = [...this.records.values()].filter(job => job.state !== 'done' && job.state !== 'failed');
        const failed = [...this.records.values()].filter(job => job.state === 'failed').length;
        const now = tickOrUndefined(currentTick);
        let lag = 0n;
        if (now !== undefined) {
            for (const job of values) {
                const at = tickOrUndefined(job.enqueuedTick);
                if (at !== undefined && now > at && now - at > lag) lag = now - at;
            }
        }
        metrics.gauge('deferred.queued', values.length);
        metrics.gauge('deferred.failed', failed);
        metrics.gauge('deferred.lagTicks', Number(lag));
    }
}
export default DeferredWorkService;
