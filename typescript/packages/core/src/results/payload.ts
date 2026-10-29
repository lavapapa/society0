import type { StoredValue, PayloadRef, PayloadRange } from '../contracts/data.js';
import type { Json } from '../contracts/common.js';
import type { ArtifactsLocal } from '../artifacts-local/index.js';
import type { SqliteTransaction } from '../sqlite/contracts.js';
import { fail } from '../information/page.js';
export async function* chunks(buffer: Uint8Array, chunkBytes = 65536) {
    for (let offset = 0; offset < buffer.length; offset += chunkBytes)
        yield buffer.subarray(offset, offset + chunkBytes);
}
export async function prepareValue(value: Json, inline: number, artifacts: ArtifactsLocal, signal?: AbortSignal): Promise<StoredValue> {
    const raw = Buffer.from(JSON.stringify(value));
    if (raw.length <= inline)
        return { kind: 'inline', value };
    const object = await artifacts.stage(chunks(raw, artifacts.chunkBytes), 'application/json', { signal });
    return { kind: 'reference', ref: { object, codec: 'json/utf8', rawOffset: '0', rawBytes: String(raw.length) } };
}
export async function attachValue(tx: SqliteTransaction, value: StoredValue, artifacts: ArtifactsLocal) {
    if (value.kind === 'reference')
        await artifacts.reference(tx, value.ref.object);
}
export async function* readValue(ref: PayloadRef, range: PayloadRange, artifacts: ArtifactsLocal, signal?: AbortSignal) {
    if (ref.codec !== 'json/utf8' && ref.codec !== 'raw/utf8')
        return fail('PAYLOAD_CODEC');
    const offset = Number(range.offset), length = range.length;
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(length) || length < 0 || length > range.maxBytes || offset + length > Number(ref.rawBytes))
        return fail('PAYLOAD_RANGE');
    yield* artifacts.read(ref.object, { offset: String(BigInt(ref.rawOffset) + BigInt(offset)), length }, { signal });
}
