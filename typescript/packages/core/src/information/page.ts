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
export function page<T>(items: readonly T[], total: string, revision: string, request: PageRequest, id: unknown, key: (v: T) => unknown, more = false): Page<T> {
    bounds(request);
    const selected = items.slice(0, request.maxItems);
    while (true) {
        const hasMore = more || selected.length < items.length;
        const result = { items: [...selected], total, revision, ...(hasMore && selected.length ? { next: cursor(id, key(selected[selected.length - 1]!)) } : {}), responseBytes: 0 };
        try {
            return sized(result, request.maxResponseBytes);
        }
        catch (error) {
            if (selected.length <= 1)
                throw error;
            selected.pop();
        }
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
