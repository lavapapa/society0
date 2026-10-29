import type { Page, PageRequest } from './contracts.js';
export function bytes(value: unknown) {
    return Buffer.byteLength(JSON.stringify(value));
}
export function cursor(identity: unknown, last: string) {
    return Buffer.from(JSON.stringify({
        identity, last
    })).toString('base64url');
}
export function after(value: string | undefined, identity: unknown) {
    if (!value)
        return '';
    let parsed;
    try {
        parsed = JSON.parse(Buffer.from(value, 'base64url').toString());
    }
    catch {
        throw Error('SOCIAL_CURSOR_INVALID');
    }
    if (JSON.stringify(parsed.identity) !== JSON.stringify(identity) || typeof parsed.last !== 'string')
        throw Error('SOCIAL_CURSOR_STALE');
    return parsed.last as string;
}
export function bounds(request: PageRequest) {
    if (!Number.isSafeInteger(request.maxItems) || request.maxItems < 1 || !Number.isSafeInteger(request.maxResponseBytes) || request.maxResponseBytes < 1)
        throw Error('SOCIAL_PAGE_BUDGET');
}
export function sized<T extends {
    responseBytes: number;
}>(value: T, max: number): T {
    for (let i = 0; i < 8; i++) {
        const size = bytes(value);
        if (size === value.responseBytes) {
            if (size > max)
                throw Error('SOCIAL_PAGE_BYTES');
            return value;
        }
        value.responseBytes = size;
    }
    throw Error('SOCIAL_PAGE_BYTES');
}
export function page<T>(rows: readonly T[], total: string, revision: string, request: PageRequest, identity: unknown, key: (item: T) => string): Page<T> {
    bounds(request);
    for (let n = Math.min(rows.length, request.maxItems); n >= 0; n--) {
        const items = rows.slice(0, n);
        const more = n < rows.length;
        const next = more && items.length ? cursor(identity, key(items[items.length - 1]!)) : undefined;
        const result = {
            items, total, revision, ...(next ? {
                next
            } : {}), responseBytes: 0
        };
        try {
            const value = sized(result, request.maxResponseBytes);
            if (!n && rows.length)
                throw Error('SOCIAL_ITEM_BYTES');
            return value;
        }
        catch (error) {
            if (n === 0)
                throw error;
        }
    }
    throw Error('SOCIAL_PAGE_BYTES');
}
export function transactionRequest(operationId: string) {
    return {
        operationId, maxDurationMs: 15000, maxStatements: 1024, maxInputBytes: 1048576, maxChangedRows: 10000, maxCapturedBytes: 4194304
    };
}
