import { Context } from '@society0/cordis';
import Schema from '@society0/schemastery';
import { setImmediate } from 'node:timers/promises';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import '../vector-providers/index.js';
import { compareCursor, sameIdentity, sameProjection, vectorFault, type MemoryIndex, type EmbeddingIdentity, type MemoryChange, type MemoryChangeCursor, type MemoryIndexProgress, type VectorDistance } from '../vector-providers/types.js';
export interface ExactOptions {
    runId: string;
    generation: string;
    space: string;
    identity: EmbeddingIdentity;
    distance: VectorDistance;
    maxItems: number;
    maxBytes: number;
    maxBatchItems: number;
    maxQueryItems: number;
}
export const Config = Schema.object({ name: Schema.string().default('memory-exact'), generation: Schema.string().required(), space: Schema.string().required(), identity: Schema.object({ provider: Schema.string().required(), configId: Schema.string().required(), model: Schema.string().required(), dimensions: Schema.natural().min(1).max(65536).required() }).required(), distance: Schema.union(['cosine', 'euclidean', 'dot']).default('cosine'), maxItems: Schema.natural().min(1).default(10000), maxBytes: Schema.natural().min(1).default(16777216), maxBatchItems: Schema.natural().min(1).default(256), maxQueryItems: Schema.natural().min(1).default(100) });
export const metadata: PluginMetadata = { title: 'vector-exact', summary: 'Bounded exact vector reference projection', configVersion: 1, configPolicy: [], scope: 'run', allowedScopes: ['run'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './vector-exact', provides: [], requires: [{ service: { key: 'vectorProviders', protocol: 'vectorProviders', major: 1, capabilities: [] }, optional: false, phase: 'module' }] };
interface Entry {
    vector: Float64Array;
    bytes: number;
}
export class ExactMemoryIndex implements MemoryIndex {
    readonly runId: string;
    readonly generation: string;
    readonly space: string;
    readonly identity: EmbeddingIdentity;
    readonly distance: VectorDistance;
    private entries = new Map<string, Entry>();
    private bytes = 0;
    private after?: MemoryChangeCursor;
    private closed = false;
    private closing?: Promise<void>;
    private active = new Set<Promise<unknown>>();
    private applying = false;
    constructor(private options: ExactOptions) {
        for (const value of [options.maxItems, options.maxBytes, options.maxBatchItems, options.maxQueryItems, options.identity.dimensions])
            if (!Number.isSafeInteger(value) || value < 1)
                vectorFault('VECTOR_BUDGET');
        this.runId = options.runId;
        this.generation = options.generation;
        this.space = options.space;
        this.identity = Object.freeze({ ...options.identity });
        this.distance = options.distance;
    }
    private admit(signal: AbortSignal) { if (this.closed)
        vectorFault('VECTOR_CLOSED'); signal.throwIfAborted(); }
    private validate(vector: readonly number[]) { if (vector.length !== this.identity.dimensions)
        vectorFault('VECTOR_DIMENSIONS'); if (vector.some(value => !Number.isFinite(value)))
        vectorFault('VECTOR_NONFINITE'); if (this.distance === 'cosine' && !vector.some(value => value !== 0))
        vectorFault('VECTOR_ZERO_NORM'); }
    private track<T>(job: Promise<T>): Promise<T> { this.active.add(job); void job.finally(() => this.active.delete(job)).catch(() => { }); return job; }
    async apply(changes: readonly MemoryChange[], signal: AbortSignal): Promise<void> {
        this.admit(signal);
        if (this.applying)
            vectorFault('VECTOR_BUSY');
        this.applying = true;
        try {
            await this.track(this.applyChanges(changes, signal));
        }
        finally {
            this.applying = false;
        }
    }
    private async applyChanges(changes: readonly MemoryChange[], signal: AbortSignal): Promise<void> {
        this.admit(signal);
        if (changes.length > this.options.maxBatchItems)
            vectorFault('VECTOR_BATCH_BUDGET');
        for (let i = 0; i < changes.length; i++) {
            if (i && compareCursor(changes[i - 1]!.cursor, changes[i]!.cursor) >= 0)
                vectorFault('VECTOR_CHANGE_ORDER');
            if (i % 64 === 0) {
                await setImmediate();
                this.admit(signal);
            }
            const change = changes[i]!;
            if (this.after && compareCursor(change.cursor, this.after) <= 0)
                continue;
            if (change.kind === 'delete')
                this.remove(change.cursor.id);
            else
                this.upsert(change);
            this.after = Object.freeze({ ...change.cursor });
        }
    }
    private remove(id: string): void { const previous = this.entries.get(id); if (!previous)
        return; this.entries.delete(id); this.bytes -= previous.bytes; }
    private upsert(change: Extract<MemoryChange, {
        kind: 'upsert';
    }>): void {
        if (!sameIdentity(change.vector.identity, this.identity))
            vectorFault('VECTOR_IDENTITY_MISMATCH');
        this.validate(change.vector.vector);
        const previous = this.entries.get(change.cursor.id), oldBytes = previous?.bytes ?? 0, bytes = Buffer.byteLength(change.cursor.id) + change.vector.vector.length * 8 + 64;
        if ((!previous && this.entries.size >= this.options.maxItems) || this.bytes - oldBytes + bytes > this.options.maxBytes)
            vectorFault('VECTOR_STORAGE_BUDGET');
        this.entries.set(change.cursor.id, { vector: Float64Array.from(change.vector.vector), bytes });
        this.bytes += bytes - oldBytes;
    }
    private checkCursor(progress: MemoryIndexProgress): void {
        if (Boolean(progress.after) !== Boolean(this.after))
            vectorFault('VECTOR_PROGRESS_MISMATCH');
        if (!this.after)
            return;
        if (compareCursor(progress.after!, this.after) !== 0)
            vectorFault('VECTOR_PROGRESS_MISMATCH');
        if (BigInt(progress.throughRevision) < BigInt(this.after.revision))
            vectorFault('VECTOR_PROGRESS_MISMATCH');
    }
    private checkProgress(progress: MemoryIndexProgress): void {
        if (progress.runId !== this.runId)
            vectorFault('VECTOR_RUN_MISMATCH');
        if (!sameProjection(progress, this))
            vectorFault('VECTOR_IDENTITY_MISMATCH');
        this.checkCursor(progress);
    }
    private measure(query: readonly number[], candidate: Float64Array): number {
        let dot = 0, left = 0, right = 0, square = 0;
        for (let i = 0; i < query.length; i++) {
            const a = query[i]!, b = candidate[i]!;
            dot += a * b;
            left += a * a;
            right += b * b;
            square += (a - b) ** 2;
        }
        const value = this.distance === 'dot' ? -dot : this.distance === 'euclidean' ? Math.sqrt(square) : 1 - dot / Math.sqrt(left * right);
        if (!Number.isFinite(value))
            vectorFault('VECTOR_DISTANCE_NONFINITE');
        return value;
    }
    async query(vector: readonly number[], limit: number, progress: MemoryIndexProgress, signal: AbortSignal): Promise<readonly {
        id: string;
        distance: number;
    }[]> { return this.track(this.queryEntries(vector, limit, progress, signal)); }
    private async queryEntries(vector: readonly number[], limit: number, progress: MemoryIndexProgress, signal: AbortSignal): Promise<readonly {
        id: string;
        distance: number;
    }[]> {
        this.admit(signal);
        this.checkProgress(progress);
        this.validate(vector);
        if (!Number.isSafeInteger(limit) || limit < 1 || limit > this.options.maxQueryItems)
            vectorFault('VECTOR_QUERY_BUDGET');
        const selected: {
            id: string;
            distance: number;
        }[] = [];
        let scanned = 0;
        for (const [id, entry] of this.entries) {
            if (scanned++ % 64 === 0) {
                await setImmediate();
                this.admit(signal);
                this.checkProgress(progress);
            }
            selected.push({ id, distance: this.measure(vector, entry.vector) });
            selected.sort((a, b) => a.distance - b.distance || Buffer.compare(Buffer.from(a.id), Buffer.from(b.id)));
            if (selected.length > limit)
                selected.pop();
        }
        this.checkProgress(progress);
        return selected;
    }
    async reset(signal: AbortSignal): Promise<void> { this.admit(signal); if (this.active.size)
        vectorFault('VECTOR_BUSY'); this.entries.clear(); this.bytes = 0; this.after = undefined; }
    close(): Promise<void> { return this.closing ??= (async () => { this.closed = true; await Promise.allSettled([...this.active]); this.entries.clear(); this.bytes = 0; this.after = undefined; })(); }
    diagnostics() { return { items: this.entries.size, bytes: this.bytes, after: this.after, closed: this.closed }; }
}
function apply(ctx: Context, config: ReturnType<typeof Config>) { ctx.vectorProviders.register(config.name, new ExactMemoryIndex({ ...config, runId: ctx.vectorProviders.runId })); }
export default { Config, inject: ['vectorProviders'], apply };
