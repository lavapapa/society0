import { mkdir, mkdtemp, open, rm, type FileHandle } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import type { Json } from '@society0/core/contracts/common';
import { datasetFault, type DatasetCodec } from './codec.js';
import { compress, footer, numbers, writeAll, readFile, split, frameEntryBytes, recordEntryBytes, type Directory } from './format.js';
export interface PrepareOptions {
    maxRecordBytes: number;
    frameBytes: number;
    maxPendingBytes: number;
    signal: AbortSignal;
}
export interface EncodeOptions extends PrepareOptions {
    directory: string;
    chunkBytes: number;
}
async function* singleton(value: Json) { yield value; }
class FrameEncoder {
    private raw: Buffer;
    private used = 0;
    private rawBytes = 0;
    private storedBytes = 0;
    private frames = 0;
    private records = 0;
    constructor(private options: EncodeOptions, private codec: DatasetCodec, private frameFile: FileHandle, private recordFile: FileHandle) { this.raw = Buffer.allocUnsafe(options.frameBytes); }
    private async *flush(): AsyncIterable<Uint8Array> {
        if (!this.used)
            return;
        this.options.signal.throwIfAborted();
        const encoded = await compress(this.raw.subarray(0, this.used), { maxOutputLength: this.options.frameBytes * 2 + 64 });
        this.options.signal.throwIfAborted();
        await writeAll(this.frameFile, numbers([this.rawBytes - this.used, this.used, this.storedBytes, encoded.length]));
        this.frames++;
        this.storedBytes += encoded.length;
        this.used = 0;
        yield* split(encoded, this.options.chunkBytes);
    }
    private async *append(bytes: Uint8Array): AsyncIterable<Uint8Array> { let offset = 0; while (offset < bytes.length) {
        const length = Math.min(bytes.length - offset, this.raw.length - this.used);
        this.raw.set(bytes.subarray(offset, offset + length), this.used);
        this.used += length;
        this.rawBytes += length;
        offset += length;
        if (this.used === this.raw.length)
            yield* this.flush();
    } }
    private async *record(value: Json): AsyncIterable<Uint8Array> {
        const start = this.rawBytes;
        let bytes = 0;
        for await (const chunk of this.codec.encode(singleton(value), { maxRecordBytes: this.options.maxRecordBytes })) {
            this.options.signal.throwIfAborted();
            bytes += chunk.length;
            if (bytes > this.options.maxRecordBytes)
                datasetFault('DATASET_RECORD_BUDGET');
            yield* this.append(chunk);
        }
        await writeAll(this.recordFile, numbers([start, bytes]));
        this.records++;
    }
    async *encode(rows: AsyncIterable<Json>, framePath: string, recordPath: string): AsyncIterable<Uint8Array> {
        for await (const row of rows) {
            this.options.signal.throwIfAborted();
            yield* this.record(row);
        }
        yield* this.flush();
        const codec = Buffer.from(this.codec.id);
        if (codec.length > 1024)
            datasetFault('DATASET_CODEC_BUDGET');
        const directory: Directory = { records: this.records, rawBytes: this.rawBytes, frames: this.frames, framesOffset: this.storedBytes, recordsOffset: this.storedBytes + this.frames * frameEntryBytes, frameBytes: this.options.frameBytes, codecBytes: codec.length, codec: this.codec.id };
        yield* readFile(framePath, this.options.chunkBytes);
        yield* readFile(recordPath, this.options.chunkBytes);
        yield codec;
        yield footer(directory);
    }
}
export async function* encodeFrames(rows: AsyncIterable<Json>, codec: DatasetCodec, options: EncodeOptions): AsyncIterable<Uint8Array> {
    for (const value of [options.maxRecordBytes, options.frameBytes, options.maxPendingBytes, options.chunkBytes])
        if (!Number.isSafeInteger(value) || value < 1)
            datasetFault('DATASET_BUDGET');
    if (options.frameBytes < 1024 || 3 * options.maxRecordBytes + 3 * options.frameBytes + options.chunkBytes > options.maxPendingBytes)
        datasetFault('DATASET_PENDING_BUDGET');
    const directory = resolve(options.directory);
    await mkdir(directory, { recursive: true });
    const owned = await mkdtemp(join(directory, 'prepare-'));
    const frames = join(owned, 'frames'), records = join(owned, 'records');
    const handles: FileHandle[] = [];
    try {
        const frameFile = await open(frames, 'wx');
        handles.push(frameFile);
        const recordFile = await open(records, 'wx');
        handles.push(recordFile);
        yield* new FrameEncoder(options, codec, frameFile, recordFile).encode(rows, frames, records);
    }
    finally {
        await Promise.all(handles.map(handle => handle.close()));
        await rm(owned, { recursive: true, force: true });
    }
}
