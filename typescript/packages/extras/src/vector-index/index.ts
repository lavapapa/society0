import { Context, Service } from '@society0/cordis';
import Schema from '@society0/schemastery';
import { randomUUID } from 'node:crypto';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { TaskScope } from '@society0/host/tasks';
import type { SqliteReadView } from '@society0/core/sqlite';
import type { DeferredWork } from '@society0/core/contracts/deferred-work';
import '@society0/host/tasks';
import '@society0/core/sqlite';
import '@society0/core/execution-fence';
import '../memory-repository/index.js';
import '../vector-providers/index.js';
import { vectorFault, sameProjection, type MemoryIndex, type MemoryIndexProgress, type MemoryChangeCursor } from '../vector-providers/types.js';
export const VECTOR_CATCH_UP_JOB = 'vector-index.catch-up';
export const Config = Schema.object({ maxLag: Schema.natural().default(0), provider: Schema.string().default('memory-exact'), batchRows: Schema.natural().min(1).default(32), batchBytes: Schema.natural().min(1).default(262144), snapshotMs: Schema.natural().min(1).default(30000), queryLimit: Schema.natural().min(1).default(10) });
export const metadata: PluginMetadata = { title: 'vector-index', summary: 'Rebuildable vector projection with SQL source and explicit progress', configVersion: 1, configPolicy: [{ path: '/maxLag', mode: 'new-run', affectsResults: true }, { path: '/provider', mode: 'new-run', affectsResults: true }, { path: '/batchRows', mode: 'new-run', affectsResults: true }, { path: '/batchBytes', mode: 'new-run', affectsResults: true }, { path: '/snapshotMs', mode: 'new-run', affectsResults: true }, { path: '/queryLimit', mode: 'new-run', affectsResults: true }], scope: 'run', allowedScopes: ['run'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './vector-index', provides: [{ key: 'vectorIndex', protocol: 'vectorIndex', major: 1, capabilities: [] }], requires: ['memoryRepository', 'tasks', 'vectorProviders', 'database', 'executionFence'].map(key => ({ service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module' })) };
declare module '@society0/cordis' {
    interface Context {
        vectorIndex: VectorIndex;
    }
}
export class VectorIndex extends Service {
    static Config = Config;
    static inject = ['memoryRepository', 'tasks', 'vectorProviders', 'database', 'executionFence'];
    private owner!: Context;
    private tasks!: TaskScope;
    private busy = false;
    constructor(ctx: Context, private options: ReturnType<typeof Config>) { super(ctx, 'vectorIndex'); }
    [Service.init]() {
        this.owner = this.ctx;
        this.tasks = this.ctx.tasks.bind();
        this.ctx.inject(['deferredWork'], scoped => {
            const deferred = scoped.get('deferredWork') as DeferredWork;
            scoped.effect(() => deferred.register(VECTOR_CATCH_UP_JOB, {
                run: async (job, context) => {
                    const inputs = job.inputs as { readonly revision?: unknown } | null;
                    if (typeof inputs?.revision !== 'string')
                        vectorFault('VECTOR_JOB_INPUT');
                    const progress = await this.prepareCatchUp(inputs.revision, context.signal);
                    return {
                        output: { revision: inputs.revision, provider: this.options.provider },
                        write: async tx => { await this.owner.memoryRepository.recordProgress(tx, progress); }
                    };
                }
            }));
        });
    }
    /** 把追赶登记到 deferred-work；没有该服务时返回 undefined，调用方仍可用 catchUp。 */
    scheduleCatchUp(revision: string): string | undefined {
        const deferred = this.owner.get('deferredWork', false) as DeferredWork | undefined;
        if (!deferred) return undefined;
        return deferred.enqueue({
            kind: VECTOR_CATCH_UP_JOB, inputs: { revision, provider: this.options.provider },
            produces: 'vector-index/' + this.options.provider, maxLagTicks: this.options.maxLag
        });
    }
    private progress(index: MemoryIndex, throughRevision: string, after?: MemoryChangeCursor): MemoryIndexProgress { return { runId: index.runId, generation: index.generation, space: index.space, identity: index.identity, distance: index.distance, throughRevision, ...(after ? { after } : {}) }; }
    private async schedule(label: string, body: (signal: AbortSignal) => Promise<void>, signal?: AbortSignal): Promise<void> {
        if (this.busy)
            vectorFault('VECTOR_INDEX_BUSY');
        this.busy = true;
        try {
            const task = this.tasks.run(label, body, { signal, bytes: this.options.batchBytes });
            try {
                await task;
            }
            finally {
                await task.settled;
            }
        }
        finally {
            this.busy = false;
        }
    }
    catchUp(revision: string, options: {
        signal?: AbortSignal;
    } = {}): Promise<void> {
        const deferred = this.owner.get('deferredWork', false) as DeferredWork | undefined;
        if (deferred) {
            this.scheduleCatchUp(revision);
            return Promise.resolve();
        }
        return this.schedule('vector.catch-up', signal => this.update(revision, false, signal), options.signal);
    }
    rebuild(options: {
        signal?: AbortSignal;
    } = {}): Promise<void> { return this.schedule('vector.rebuild', signal => this.update(undefined, true, signal), options.signal); }
    private async save(progress: MemoryIndexProgress, signal: AbortSignal): Promise<void> {
        signal.throwIfAborted();
        const operationId = 'vector-progress:' + randomUUID();
        const reservation = await this.owner.executionFence.reserve({ id: operationId });
        try {
            signal.throwIfAborted();
            await this.owner.database.transaction(reservation.lease, { operationId, maxDurationMs: 10000, maxStatements: 4, maxInputBytes: 16384, maxChangedRows: 4, maxCapturedBytes: 65536 }, tx => this.owner.memoryRepository.recordProgress(tx, progress));
        }
        finally {
            await reservation.release();
        }
    }
    private async update(revision: string | undefined, rebuild: boolean, signal: AbortSignal): Promise<void> {
        const lease = await this.owner.vectorProviders.acquire(this.options.provider);
        try {
            const index = lease.value;
            if (index.runId !== this.owner.database.runId)
                vectorFault('VECTOR_RUN_MISMATCH');
            const view = await this.owner.database.snapshot({ maxAgeMs: this.options.snapshotMs });
            try {
                const saved = await this.owner.memoryRepository.progress(view, index.generation);
                if (saved && !sameProjection(saved, index))
                    vectorFault('VECTOR_PROGRESS_IDENTITY');
                const target = revision ?? view.revision;
                if (BigInt(target) > BigInt(view.revision))
                    vectorFault('VECTOR_REVISION_AHEAD');
                if (rebuild) {
                    await index.reset(signal);
                    const after = await this.restore(index, view, signal);
                    await this.save(this.progress(index, target, after), signal);
                }
                else
                    await this.save(await this.advance(index, view, target, signal, saved), signal);
            }
            finally {
                await view.close();
            }
        }
        finally {
            await lease.release();
        }
    }
    /** 后台追赶的只读准备：应用批处理后返回进度，由 deferred 完成事务统一落库。 */
    private async prepareCatchUp(revision: string, signal: AbortSignal): Promise<MemoryIndexProgress> {
        const lease = await this.owner.vectorProviders.acquire(this.options.provider);
        try {
            const index = lease.value;
            if (index.runId !== this.owner.database.runId)
                vectorFault('VECTOR_RUN_MISMATCH');
            const view = await this.owner.database.snapshot({ maxAgeMs: this.options.snapshotMs });
            try {
                const saved = await this.owner.memoryRepository.progress(view, index.generation);
                if (saved && !sameProjection(saved, index))
                    vectorFault('VECTOR_PROGRESS_IDENTITY');
                if (BigInt(revision) > BigInt(view.revision))
                    vectorFault('VECTOR_REVISION_AHEAD');
                return await this.advance(index, view, revision, signal, saved);
            }
            finally { await view.close(); }
        }
        finally { await lease.release(); }
    }
    private async advance(index: MemoryIndex, view: SqliteReadView, target: string, signal: AbortSignal, saved?: MemoryIndexProgress): Promise<MemoryIndexProgress> {
        if (saved && BigInt(saved.throughRevision) > BigInt(target))
            vectorFault('VECTOR_REVISION_MISMATCH');
        let after = saved?.after;
        while (true) {
            signal.throwIfAborted();
            const batch = await this.owner.memoryRepository.changes(view, { after, throughRevision: target, maxItems: this.options.batchRows, maxResponseBytes: this.options.batchBytes });
            if (!batch.items.length)
                break;
            await index.apply(batch.items, signal);
            after = batch.items.at(-1)!.cursor;
        }
        return this.progress(index, target, after);
    }
    private async restore(index: MemoryIndex, view: SqliteReadView, signal: AbortSignal): Promise<MemoryChangeCursor | undefined> {
        let after: MemoryChangeCursor | undefined;
        while (true) {
            signal.throwIfAborted();
            const batch = await this.owner.memoryRepository.vectors(view, { after, maxItems: this.options.batchRows, maxResponseBytes: this.options.batchBytes });
            if (!batch.length)
                break;
            await index.apply(batch, signal);
            after = batch.at(-1)!.cursor;
        }
        const tail = await this.owner.memoryRepository.lastChange(view);
        if (tail) {
            await index.apply([tail], signal);
            after = tail.cursor;
        }
        return after;
    }
    async query(vector: readonly number[], revision: string, options: {
        limit?: number;
        signal?: AbortSignal;
    } = {}): Promise<readonly {
        id: string;
        distance: number;
    }[]> {
        if (this.busy)
            vectorFault('VECTOR_INDEX_BUSY');
        const lease = await this.owner.vectorProviders.acquire(this.options.provider);
        try {
            const view = await this.owner.database.snapshot({ maxAgeMs: this.options.snapshotMs });
            try {
                const progress = await this.owner.memoryRepository.progress(view, lease.value.generation);
                const lag = progress ? BigInt(revision) - BigInt(progress.throughRevision) : -1n;
                if (!progress || lag < 0n || lag > BigInt(this.options.maxLag))
                    vectorFault('VECTOR_INDEX_NOT_READY');
                return await lease.value.query(vector, options.limit ?? this.options.queryLimit, progress, options.signal ?? new AbortController().signal);
            }
            finally {
                await view.close();
            }
        }
        finally {
            await lease.release();
        }
    }
    describe() { return { provider: this.options.provider, busy: this.busy }; }
}
export default VectorIndex;
