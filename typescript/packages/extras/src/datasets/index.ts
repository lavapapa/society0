import { Context, Service } from '@society0/cordis';
import Schema from '@society0/schemastery';
import { randomUUID } from 'node:crypto';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { TaskScope } from '@society0/host/tasks';
import type { Json } from '@society0/core/contracts/common';
import type { ArtifactRef } from '@society0/core/artifacts-local';
import type { SqliteMigration, SqliteReadView, SqliteTransaction } from '@society0/core/sqlite';
import type { PageRequest, PayloadRef, PayloadRange } from '@society0/core/contracts/data';
import { after, page } from '@society0/core/information/page';
import '@society0/core/artifacts-local';
import '@society0/core/sqlite';
import '@society0/host/tasks';
import { datasetCodec, datasetFault, validateCodec, type DatasetCodec } from './codec.js';
import { encodeFrames, type PrepareOptions } from './encode.js';
import { FrameReader, type DatasetMetrics } from './read.js';
export { datasetCodec, type DatasetCodec, type PrepareOptions };
export const Config = Schema.object({ directory: Schema.string().default('run.datasets'), codec: Schema.string().default('jsonl/v1'), chunkBytes: Schema.natural().min(64).default(4096), maxFrameBytes: Schema.natural().min(1024).default(1048576), maxPageItems: Schema.natural().min(1).default(256), maxReadBytes: Schema.natural().min(1).default(1048576) });
export const metadata: PluginMetadata = { title: 'datasets', summary: 'Indexed immutable framed datasets and bounded reference pages', configVersion: 1, configPolicy: [{ path: '/directory', mode: 'new-run', affectsResults: true }, { path: '/codec', mode: 'new-run', affectsResults: true }, { path: '/chunkBytes', mode: 'new-run', affectsResults: true }, { path: '/maxFrameBytes', mode: 'new-run', affectsResults: true }, { path: '/maxPageItems', mode: 'new-run', affectsResults: true }, { path: '/maxReadBytes', mode: 'new-run', affectsResults: true }], scope: 'generation', allowedScopes: ['generation'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './datasets', provides: [{ key: 'datasets', protocol: 'datasets', major: 1, capabilities: [] }], slots: { requires: ['society/dataset-codec'] }, requires: ['database', 'artifacts', 'slots', 'tasks'].map(key => ({ service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module' })) };
export function datasetsMigration(source: Readonly<Record<string, unknown>>): SqliteMigration { return { owner: 'datasets', from: 0, to: 1, after: ['artifacts-local'], source, statements: [{ sql: 'CREATE TABLE datasets(id TEXT PRIMARY KEY NOT NULL,object TEXT NOT NULL);' }] }; }
function datasetId(id: string): string { if (!id.isWellFormed()) throw Object.assign(new Error('DATASET_ID: id contains a lone surrogate'), { code: 'DATASET_ID', field: 'id' }); return id; }
declare module '@society0/cordis' {
    interface Context {
        datasets: Datasets;
    }
}
export class Datasets extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts', 'slots', 'tasks'];
    private owner!: Context;
    private tasks!: TaskScope;
    private reader!: FrameReader;
    private instance = randomUUID();
    private metrics: DatasetMetrics = { framesDecompressed: 0, storedFrameBytes: 0, nativeDecodedBytes: 0, hostCopiedBytes: 0, metadataBytes: 0, activeDecodedBytes: 0 };
    constructor(ctx: Context, private options: ReturnType<typeof Config>) { super(ctx, 'datasets'); }
    [Service.init]() { this.owner = this.ctx; this.tasks = this.ctx.tasks.bind(); this.reader = new FrameReader(this.ctx.artifacts, this.options.maxFrameBytes, this.metrics); this.ctx.slots.declare({ key: datasetCodec, strategy: 'keyed', required: true, validate: validateCodec }); }
    private async codec(name: string) { const lease = await this.owner.slots.acquire(datasetCodec, { generation: this.instance }); const item = lease.items.find(item => item.id === name); if (!item) {
        await lease.release();
        datasetFault('DATASET_CODEC_UNAVAILABLE');
    } return { value: item.value, release: () => lease.release() }; }
    async prepare(rows: AsyncIterable<Json>, options: PrepareOptions): Promise<ArtifactRef> {
        if (options.frameBytes > this.options.maxFrameBytes)
            datasetFault('DATASET_FRAME_BUDGET');
        const task = this.tasks.run('datasets.prepare', async (signal) => { const codec = await this.codec(this.options.codec); try {
            return await this.owner.artifacts.stage(encodeFrames(rows, codec.value, { ...options, signal, directory: this.options.directory, chunkBytes: this.options.chunkBytes }), 'application/vnd.society.dataset+zstd', { signal });
        }
        finally {
            await codec.release();
        } }, { signal: options.signal, bytes: options.maxPendingBytes });
        try {
            return await task;
        }
        finally {
            await task.settled;
        }
    }
    private run(reader: SqliteReadView | SqliteTransaction) { if (reader.runId !== this.owner.database.runId)
        datasetFault('DATASET_RUN_MISMATCH'); }
    async attach(tx: SqliteTransaction, id: string, object: ArtifactRef): Promise<void> { this.run(tx); datasetId(id); await this.owner.artifacts.reference(tx, object); await tx.execute({ sql: 'INSERT INTO datasets VALUES(?,?)', parameters: [id, JSON.stringify(object)] }); }
    private async object(id: string, view: SqliteReadView): Promise<ArtifactRef> { this.run(view); datasetId(id); const [row] = await view.query({ sql: 'SELECT object FROM datasets WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: 16384 }); if (!row)
        datasetFault('DATASET_UNKNOWN'); return JSON.parse(String(row.object)); }
    async describe(id: string, view: SqliteReadView) { const object = await this.object(id, view); const directory = await this.reader.directory(object); return { id, records: String(directory.records), rawBytes: String(directory.rawBytes), codec: directory.codec }; }
    async page(id: string, view: SqliteReadView, request: PageRequest) {
        if (!Number.isSafeInteger(request.maxItems) || request.maxItems < 1 || request.maxItems > this.options.maxPageItems)
            datasetFault('DATASET_PAGE_BUDGET');
        const object = await this.object(id, view);
        const directory = await this.reader.directory(object);
        const identity = { runId: view.runId, revision: view.revision, id, object: object.id };
        const start = Number(after(request.cursor, identity) ?? 0);
        if (!Number.isSafeInteger(start) || start < 0 || start > directory.records)
            datasetFault('DATASET_CURSOR');
        const rows = await this.reader.records(object, directory, start, request.maxItems + 1);
        return page(rows, String(directory.records), view.revision, request, identity, value => Number(value.ordinal) + 1);
    }
    readPayload(ref: PayloadRef, range: PayloadRange, signal?: AbortSignal): AsyncIterable<Uint8Array> { if (range.maxBytes > this.options.maxReadBytes)
        datasetFault('DATASET_READ_BUDGET'); return this.reader.payload(ref, range, signal); }
    async get(ref: PayloadRef, options: {
        maxBytes: number;
        signal?: AbortSignal;
    }): Promise<Json> {
        if (Number(ref.rawBytes) > options.maxBytes)
            throw Object.assign(new Error('DATASET_RECORD_REFERENCE'), { code: 'DATASET_RECORD_REFERENCE', ref, rawBytes: ref.rawBytes });
        const directory = await this.reader.directory(ref.object, options.signal);
        const codec = await this.codec(directory.codec);
        try {
            let result: Json | undefined, count = 0;
            for await (const value of codec.value.decode(this.readPayload(ref, { offset: '0', length: Number(ref.rawBytes), maxBytes: options.maxBytes }, options.signal))) {
                result = value;
                count++;
            }
            if (count !== 1 || result === undefined)
                datasetFault('DATASET_RECORD_COUNT');
            return result;
        }
        finally {
            await codec.release();
        }
    }
    diagnostics() { return { ...this.metrics }; }
}
export default Datasets;
