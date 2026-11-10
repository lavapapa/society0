import type { Context } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { Json } from '@society0/core/contracts/common';
import { datasetCodec, datasetFault, type DatasetCodec } from '../datasets/codec.js';
import { measureJson } from './size.js';
export const Config = Schema.object({ maxRecordBytes: Schema.natural().min(2).default(1048576), chunkBytes: Schema.natural().min(1).default(65536) });
export const metadata: PluginMetadata = { title: 'dataset-jsonl', summary: 'Bounded JSONL codec contribution', configVersion: 1, configPolicy: [{ path: '/maxRecordBytes', mode: 'new-run', affectsResults: true }, { path: '/chunkBytes', mode: 'new-run', affectsResults: true }], scope: 'generation', allowedScopes: ['generation'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './dataset-jsonl', provides: [], slots: { provides: ['society/dataset-codec'] }, requires: [{ service: { key: 'slots', protocol: 'slots', major: 1, capabilities: [] }, optional: false, phase: 'module' }] };
export class JsonlCodec implements DatasetCodec {
    readonly id = 'jsonl/v1';
    constructor(private options: ReturnType<typeof Config>) { if (!Number.isSafeInteger(options.maxRecordBytes) || options.maxRecordBytes < 2 || !Number.isSafeInteger(options.chunkBytes) || options.chunkBytes < 1)
        datasetFault('JSONL_BUDGET'); }
    async *encode(rows: AsyncIterable<Json>, options?: {
        maxRecordBytes: number;
    }): AsyncIterable<Uint8Array> { for await (const row of rows) {
        measureJson(row, Math.min(options?.maxRecordBytes ?? this.options.maxRecordBytes, this.options.maxRecordBytes) - 1);
        const line = Buffer.from(JSON.stringify(row) + '\n');
        for (let offset = 0; offset < line.length; offset += this.options.chunkBytes)
            yield line.subarray(offset, offset + this.options.chunkBytes);
    } }
    private parse(line: Uint8Array): Json { if (!line.length)
        datasetFault('JSONL_EMPTY_RECORD'); const value: Json = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(line)); measureJson(value, this.options.maxRecordBytes); return value; }
    async *decode(chunks: AsyncIterable<Uint8Array>): AsyncIterable<Json> {
        const pending = Buffer.allocUnsafe(this.options.maxRecordBytes);
        let size = 0;
        for await (const bytes of chunks) {
            const chunk = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
            let offset = 0;
            while (offset < chunk.length) {
                const newline = chunk.indexOf(10, offset), end = newline < 0 ? chunk.length : newline;
                const length = end - offset;
                if (size + length >= this.options.maxRecordBytes)
                    datasetFault('JSONL_RECORD_BUDGET');
                chunk.copy(pending, size, offset, end);
                size += length;
                if (newline < 0)
                    break;
                yield this.parse(pending.subarray(0, size));
                size = 0;
                offset = newline + 1;
            }
        }
        if (size)
            yield this.parse(pending.subarray(0, size));
    }
}
function apply(ctx: Context, config: ReturnType<typeof Config>) { ctx.slots.contribute(datasetCodec, { id: 'jsonl/v1', value: new JsonlCodec(config) }); }
export default { Config, inject: ['slots'], apply };
