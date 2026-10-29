import type { ArtifactRef, ArtifactsLocal } from '@society0/core/artifacts-local';
import type { PayloadRef, PayloadRange, DatasetRecord } from '@society0/core/contracts/data';
import { datasetFault } from './codec.js';
import { parseFooter, footerBytes, frameEntryBytes, recordEntryBytes, numberAt, decompress, type Directory } from './format.js';
export interface DatasetMetrics {
    framesDecompressed: number;
    storedFrameBytes: number;
    nativeDecodedBytes: number;
    hostCopiedBytes: number;
    metadataBytes: number;
    activeDecodedBytes: number;
}
export class FrameReader {
    constructor(private artifacts: ArtifactsLocal, private maxFrameBytes: number, private metrics: DatasetMetrics) { }
    private async bytes(object: ArtifactRef, offset: number, length: number, signal?: AbortSignal): Promise<Buffer> {
        const chunks: Uint8Array[] = [];
        for await (const chunk of this.artifacts.read(object, { offset: String(offset), length }, { signal }))
            chunks.push(chunk);
        return Buffer.concat(chunks);
    }
    async directory(object: ArtifactRef, signal?: AbortSignal): Promise<Directory> {
        const size = Number(object.bytes);
        if (!Number.isSafeInteger(size) || size < footerBytes)
            datasetFault('DATASET_FORMAT');
        const footer = await this.bytes(object, size - footerBytes, footerBytes, signal);
        const directory = parseFooter(footer);
        if (directory.codecBytes > 1024 || directory.frameBytes < 1024 || directory.frameBytes > this.maxFrameBytes)
            datasetFault('DATASET_DIRECTORY_BUDGET');
        const codec = (await this.bytes(object, size - footerBytes - directory.codecBytes, directory.codecBytes, signal)).toString('utf8');
        this.metrics.metadataBytes += footerBytes + directory.codecBytes;
        if (directory.framesOffset + directory.frames * frameEntryBytes !== directory.recordsOffset || directory.recordsOffset + directory.records * recordEntryBytes + directory.codecBytes + footerBytes !== size)
            datasetFault('DATASET_DIRECTORY');
        return { ...directory, codec };
    }
    async records(object: ArtifactRef, directory: Directory, start: number, count: number, signal?: AbortSignal): Promise<DatasetRecord[]> {
        const selected = Math.min(count, directory.records - start);
        if (selected < 0)
            datasetFault('DATASET_CURSOR');
        const index = await this.bytes(object, directory.recordsOffset + start * recordEntryBytes, selected * recordEntryBytes, signal);
        this.metrics.metadataBytes += index.length;
        return Array.from({ length: selected }, (_, i) => ({ ordinal: String(start + i), value: { kind: 'reference', ref: { object, codec: 'dataset-zstd/v1', rawOffset: String(numberAt(index, i * recordEntryBytes)), rawBytes: String(numberAt(index, i * recordEntryBytes + 8)) } } }));
    }
    private async frame(object: ArtifactRef, directory: Directory, ordinal: number, signal?: AbortSignal): Promise<{
        raw: Buffer;
        offset: number;
    }> {
        const entry = await this.bytes(object, directory.framesOffset + ordinal * frameEntryBytes, frameEntryBytes, signal);
        this.metrics.metadataBytes += entry.length;
        const offset = numberAt(entry, 0), rawBytes = numberAt(entry, 8), storedOffset = numberAt(entry, 16), storedBytes = numberAt(entry, 24);
        if (rawBytes > this.maxFrameBytes || storedBytes > this.maxFrameBytes * 2 + 64)
            datasetFault('DATASET_FRAME_BUDGET');
        const stored = await this.bytes(object, storedOffset, storedBytes, signal);
        signal?.throwIfAborted();
        const raw = await decompress(stored, { maxOutputLength: rawBytes });
        signal?.throwIfAborted();
        if (raw.length !== rawBytes || offset !== ordinal * directory.frameBytes)
            datasetFault('DATASET_FRAME');
        this.metrics.framesDecompressed++;
        this.metrics.storedFrameBytes += storedBytes;
        this.metrics.nativeDecodedBytes += rawBytes;
        return { raw, offset };
    }
    private range(ref: PayloadRef, range: PayloadRange): {
        start: number;
        end: number;
        length: number;
    } {
        if (ref.codec !== 'dataset-zstd/v1')
            datasetFault('DATASET_PAYLOAD_CODEC');
        const offset = Number(range.offset), base = Number(ref.rawOffset), length = range.length, rawBytes = Number(ref.rawBytes);
        for (const value of [offset, base, length, rawBytes, range.maxBytes])
            if (!Number.isSafeInteger(value) || value < 0)
                datasetFault('DATASET_RANGE');
        if (length > range.maxBytes || offset + length > rawBytes)
            datasetFault('DATASET_RANGE_BUDGET');
        return { start: base + offset, end: base + offset + length, length };
    }
    async *payload(ref: PayloadRef, range: PayloadRange, signal?: AbortSignal): AsyncIterable<Uint8Array> {
        const { start, end, length } = this.range(ref, range);
        if (!length)
            return;
        const directory = await this.directory(ref.object, signal);
        if (end > directory.rawBytes)
            datasetFault('DATASET_RANGE');
        for (let ordinal = Math.floor(start / directory.frameBytes); ordinal <= Math.floor((end - 1) / directory.frameBytes); ordinal++) {
            signal?.throwIfAborted();
            const frame = await this.frame(ref.object, directory, ordinal, signal);
            this.metrics.activeDecodedBytes += frame.raw.length;
            try {
                const piece = Buffer.from(frame.raw.subarray(Math.max(0, start - frame.offset), Math.min(frame.raw.length, end - frame.offset)));
                this.metrics.hostCopiedBytes += piece.length;
                yield piece;
            }
            finally {
                this.metrics.activeDecodedBytes -= frame.raw.length;
            }
        }
    }
}
