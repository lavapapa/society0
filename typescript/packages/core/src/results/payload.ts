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
const SPECIAL = /[\u0000-\u001f"\\\ud800-\udfff]/;
const notJson = (what: string) => Object.assign(new TypeError('PAYLOAD_JSON_VALUE ' + what), { code: 'PAYLOAD_JSON_VALUE' });
const chunkBudget = () => Object.assign(new Error('PAYLOAD_CHUNK_BUDGET'), { code: 'PAYLOAD_CHUNK_BUDGET' });
const jsonBytes = (limit: number) => Object.assign(new Error('PAYLOAD_JSON_BYTES'), { code: 'PAYLOAD_JSON_BYTES', limit });
function controlExtra(code: number) {
    if (code >= 0x20)
        return 0;
    return code === 8 || code === 9 || code === 10 || code === 12 || code === 13 ? 1 : 5;
}
function quoteExtra(code: number) {
    return code === 34 || code === 92 ? 1 : 0;
}
function surrogateExtra(value: string, index: number) {
    const code = value.charCodeAt(index);
    if (code < 0xd800 || code > 0xdfff)
        return 0;
    const next = value.charCodeAt(index + 1);
    return code <= 0xdbff && next >= 0xdc00 && next <= 0xdfff ? -1 : 3;
}
function stringBytes(value: string) {
    if (!SPECIAL.test(value))
        return Buffer.byteLength(value) + 2;
    let extra = 0;
    for (let index = 0; index < value.length; index++) {
        const code = value.charCodeAt(index);
        const control = controlExtra(code);
        if (control) {
            extra += control;
            continue;
        }
        extra += quoteExtra(code);
        const surrogate = surrogateExtra(value, index);
        if (surrogate < 0) {
            index++;
            continue;
        }
        extra += surrogate;
    }
    return Buffer.byteLength(value) + 2 + extra;
}
function scalarBytes(value: unknown) {
    if (typeof value === 'string')
        return stringBytes(value);
    if (typeof value === 'number')
        return Number.isFinite(value) ? String(value).length : 4;
    if (typeof value === 'boolean')
        return value ? 4 : 5;
    if (typeof value === 'bigint')
        throw notJson('bigint');
    return 4;
}
const skip = (value: unknown) => value === undefined || typeof value === 'function' || typeof value === 'symbol';
class Exit {
    constructor(readonly object: object) { }
}
function children(value: object, stack: unknown[]) {
    stack.push(new Exit(value));
    if (Array.isArray(value)) {
        for (const item of value)
            stack.push(skip(item) ? null : item);
        return 2 + Math.max(0, value.length - 1);
    }
    let bytes = 2, count = 0;
    for (const key of Object.keys(value)) {
        const item = (value as Record<string, unknown>)[key];
        if (skip(item))
            continue;
        bytes += stringBytes(key) + 1;
        count++;
        stack.push(item);
    }
    return bytes + Math.max(0, count - 1);
}
function enter(item: object, ancestors: Set<object>) {
    if (typeof (item as { toJSON?: unknown }).toJSON === 'function')
        throw notJson('toJSON');
    if (ancestors.has(item))
        throw notJson('cycle');
    ancestors.add(item);
}
/** Native JSON.stringify semantics for UTF-8 byte count; non-JSON values throw PAYLOAD_JSON_VALUE. */
export function jsonByteBound(value: unknown, limit: number): number {
    if (value === undefined || typeof value === 'function' || typeof value === 'symbol')
        throw notJson('top-level');
    const stack: unknown[] = [value], ancestors = new Set<object>();
    let total = 0;
    while (stack.length) {
        const item = stack.pop();
        if (item instanceof Exit) {
            ancestors.delete(item.object);
            continue;
        }
        if (item === null || typeof item !== 'object')
            total += scalarBytes(item);
        else {
            enter(item, ancestors);
            total += children(item, stack);
        }
        if (total > limit)
            return limit + 1;
    }
    return total;
}
export async function* textChunks(text: string, chunkBytes: number, signal?: AbortSignal) {
    if (!Number.isSafeInteger(chunkBytes) || chunkBytes < 6)
        throw chunkBudget();
    const units = Math.floor(chunkBytes / 3);
    for (let start = 0; start < text.length;) {
        let end = Math.min(text.length, start + units);
        const code = text.charCodeAt(end - 1);
        if (end < text.length && code >= 0xd800 && code <= 0xdbff)
            end--;
        if (end === start)
            end = Math.min(text.length, start + 2);
        signal?.throwIfAborted();
        yield Buffer.from(text.slice(start, end));
        start = end;
    }
}
export async function prepareBoundedJson(value: Json, limits: { inline: number; maxBytes: number }, artifacts: ArtifactsLocal, signal?: AbortSignal): Promise<StoredValue> {
    signal?.throwIfAborted();
    if (artifacts.chunkBytes < 6)
        throw chunkBudget();
    if (jsonByteBound(value, limits.maxBytes) > limits.maxBytes)
        throw jsonBytes(limits.maxBytes);
    const text = JSON.stringify(value), size = Buffer.byteLength(text);
    if (size > limits.maxBytes)
        throw jsonBytes(limits.maxBytes);
    if (size <= limits.inline)
        return { kind: 'inline', value };
    const object = await artifacts.stage(textChunks(text, artifacts.chunkBytes, signal), 'application/json', { signal });
    if (object.bytes !== String(size))
        throw Object.assign(new Error('PAYLOAD_JSON_MISMATCH'), { code: 'PAYLOAD_JSON_MISMATCH' });
    return { kind: 'reference', ref: { object, codec: 'json/utf8', rawOffset: '0', rawBytes: String(size) } };
}
