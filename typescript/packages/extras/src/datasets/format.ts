import { createReadStream } from 'node:fs';
import type { FileHandle } from 'node:fs/promises';
import { promisify } from 'node:util';
import { zstdCompress, zstdDecompress } from 'node:zlib';
import { datasetFault } from './codec.js';
export const compress = promisify(zstdCompress), decompress = promisify(zstdDecompress);
export const footerBytes = 64, frameEntryBytes = 32, recordEntryBytes = 16, magic = 'S0DSZ001';
export interface Directory {
    records: number;
    rawBytes: number;
    frames: number;
    framesOffset: number;
    recordsOffset: number;
    frameBytes: number;
    codecBytes: number;
    codec: string;
}
export function numbers(values: readonly number[]): Buffer { const bytes = Buffer.alloc(values.length * 8); values.forEach((value, index) => bytes.writeBigUInt64LE(BigInt(value), index * 8)); return bytes; }
export function numberAt(bytes: Buffer, offset: number): number { const value = Number(bytes.readBigUInt64LE(offset)); if (!Number.isSafeInteger(value) || value < 0)
    datasetFault('DATASET_OFFSET'); return value; }
export function footer(directory: Directory): Buffer { const bytes = Buffer.alloc(footerBytes); bytes.write(magic); numbers([directory.records, directory.rawBytes, directory.frames, directory.framesOffset, directory.recordsOffset, directory.frameBytes, directory.codecBytes]).copy(bytes, 8); return bytes; }
export function parseFooter(bytes: Buffer): Omit<Directory, 'codec'> { if (bytes.length !== footerBytes || bytes.subarray(0, 8).toString() !== magic)
    datasetFault('DATASET_FORMAT'); return { records: numberAt(bytes, 8), rawBytes: numberAt(bytes, 16), frames: numberAt(bytes, 24), framesOffset: numberAt(bytes, 32), recordsOffset: numberAt(bytes, 40), frameBytes: numberAt(bytes, 48), codecBytes: numberAt(bytes, 56) }; }
export async function writeAll(file: FileHandle, bytes: Uint8Array) { let offset = 0; while (offset < bytes.length) {
    const result = await file.write(bytes, offset, bytes.length - offset);
    if (!result.bytesWritten)
        datasetFault('DATASET_SHORT_WRITE');
    offset += result.bytesWritten;
} }
export async function* readFile(path: string, chunkBytes: number): AsyncIterable<Uint8Array> { yield* createReadStream(path, { highWaterMark: chunkBytes }); }
export function* split(bytes: Uint8Array, chunkBytes: number) { for (let offset = 0; offset < bytes.length; offset += chunkBytes)
    yield bytes.subarray(offset, offset + chunkBytes); }
