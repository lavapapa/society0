import type { SelectionLease } from '@society0/host/slots';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import type { Page, PageRequest, DiscoveryScope } from '../contracts/data.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
export { isDeepStrictEqual as equal };
export const fail = (code: string): never => { throw Object.assign(new Error(code), { code }); };
export const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value));
export function bounds(request: PageRequest) {
    if (!Number.isSafeInteger(request.maxItems) || request.maxItems < 1 || !Number.isSafeInteger(request.maxResponseBytes) || request.maxResponseBytes < 1)
        fail('PAGE_BUDGET');
}
/** 原生SQL前缀的统一预算：每行预留编码开销，并限制捕获结果。 */
export function sqlPageBudget(request: PageRequest) {
    bounds(request);
    const rowReserve = 128, budget = request.maxResponseBytes;
    const limit = Math.min(request.maxItems, Math.max(1, Math.floor(budget / rowReserve)));
    return { limit, budget, rowReserve, nativeBounds: { maxRows: limit, maxBytes: Math.max(65536, budget * 2) } };
}
/** SQL以首行空payload表示原文无法放入本页。 */
export function assertSqlPagePrefix(rows: readonly Readonly<Record<string, unknown>>[]) {
    if (rows[0]?.payload === null) fail('PAGE_RESPONSE_BUDGET');
}
export function identity(scope: DiscoveryScope, view: SqliteReadView, version: unknown, query: unknown = scope.query) { return { runId: view.runId, actorId: scope.actorId, momentId: scope.momentId, logicalTime: scope.logicalTime ?? null, query, revision: view.revision, version }; }
export function cursor(identity: unknown, after: unknown) { return Buffer.from(JSON.stringify({ identity, after })).toString('base64url'); }
export function after(token: string | undefined, identity: unknown): any {
    if (!token)
        return undefined;
    let parsed;
    try {
        parsed = JSON.parse(Buffer.from(token, 'base64url').toString());
    }
    catch {
        fail('CURSOR_INVALID');
    }
    if (!isDeepStrictEqual(parsed.identity, identity))
        fail('CURSOR_STALE');
    return parsed.after;
}
export function sized<T extends {
    responseBytes: number;
}>(value: T, max: number): T {
    let n = bytes(value);
    while (n !== value.responseBytes) {
        value.responseBytes = n;
        n = bytes(value);
    }
    if (n > max)
        fail('PAGE_RESPONSE_BUDGET');
    return value;
}
export function jsonStringBytes(value: string): number {
    let size = 2;
    for (let index = 0; index < value.length; index++) {
        const code = value.charCodeAt(index);
        if (code === 0x22 || code === 0x5c)
            size += 2;
        else if (code < 0x20) {
            if (code === 0x08 || code === 0x09 || code === 0x0a || code === 0x0c || code === 0x0d)
                size += 2;
            else
                size += 6;
        }
        else if (code < 0x80)
            size += 1;
        else if (code < 0x800)
            size += 2;
        else if (code >= 0xd800 && code <= 0xdbff) {
            const next = index + 1 < value.length ? value.charCodeAt(index + 1) : 0;
            if (next >= 0xdc00 && next <= 0xdfff) {
                size += 4;
                index++;
            }
            else
                size += 6;
        }
        else if (code >= 0xdc00 && code <= 0xdfff)
            size += 6;
        else
            size += 3;
    }
    return size;
}
/** 与 JSON.stringify 的 UTF-8 长度一致，但不构造整段字符串。 */
export function jsonBytes(value: unknown): number {
    if (value === null)
        return 4;
    switch (typeof value) {
        case 'string': return jsonStringBytes(value);
        case 'number': return String(value).length === 0 ? 4 : (Number.isFinite(value) ? String(value).length : 4);
        case 'boolean': return value ? 4 : 5;
        case 'bigint': return String(value).length;
        case 'object': {
            if (Array.isArray(value)) {
                let size = 2;
                for (let index = 0; index < value.length; index++) {
                    if (index)
                        size++;
                    const item = value[index];
                    size += item === undefined || typeof item === 'function' || typeof item === 'symbol' ? 4 : jsonBytes(item);
                }
                return size;
            }
            const record = value as { toJSON?: () => unknown };
            if (typeof record.toJSON === 'function')
                return jsonBytes(record.toJSON());
            let size = 2, first = true;
            for (const [name, item] of Object.entries(value)) {
                if (item === undefined || typeof item === 'function' || typeof item === 'symbol')
                    continue;
                if (!first)
                    size++;
                first = false;
                size += jsonStringBytes(name) + 1 + jsonBytes(item);
            }
            return size;
        }
        default: return 4;
    }
}
/** 逐项累计字节数：语义与整页 JSON.stringify 相同，但只遍历一次条目。 */
export function page<T>(items: readonly T[], total: string, revision: string, request: PageRequest, id: unknown, key: (v: T) => unknown, more = false, measured?: readonly number[]): Page<T> {
    bounds(request);
    const selected = items.slice(0, request.maxItems);
    const sizes = measured ? measured.slice(0, selected.length) : selected.map(item => Buffer.byteLength(JSON.stringify(item), 'utf8'));
    while (true) {
        const hasMore = more || selected.length < items.length;
        const next = hasMore && selected.length ? cursor(id, key(selected[selected.length - 1]!)) : undefined;
        const envelope = Buffer.byteLength(JSON.stringify({ items: [], total, revision, ...(next === undefined ? {} : { next }), responseBytes: 0 }), 'utf8');
        let content = envelope;
        if (selected.length) {
            let itemsBytes = 0;
            for (let index = 0; index < selected.length; index++)
                itemsBytes += sizes[index]!;
            content += itemsBytes + selected.length - 1;
        }
        let responseBytes = 0;
        while (true) {
            const candidate = content + String(responseBytes).length - 1;
            if (candidate === responseBytes)
                break;
            responseBytes = candidate;
        }
        if (responseBytes > request.maxResponseBytes) {
            if (selected.length <= 1)
                fail('PAGE_RESPONSE_BUDGET');
            selected.pop();
            sizes.pop();
            continue;
        }
        return {
            items: [...selected], total, revision, ...(next === undefined ? {} : { next }), responseBytes
        };
    }
}
/** 注册变更使用实例身份和递增版本；游标大小与注册数量无关。 */
export class RegistryClock {
    private readonly id = randomUUID();
    private version = 0;
    private previous: readonly string[] = [];
    async capture<T extends { readonly registrationId: string }>(pending: Promise<SelectionLease<T>>) {
        const selected = await pending;
        try {
            const state = this.observe(selected.items.map(item => item.value.registrationId));
            return { owner: state.id, version: String(state.version) };
        } finally {
            await selected.release();
        }
    }
    observe(ids: readonly string[]) {
        if (!isDeepStrictEqual(ids, this.previous)) {
            this.previous = ids;
            this.version++;
        }
        return { id: this.id, version: this.version };
    }
}
