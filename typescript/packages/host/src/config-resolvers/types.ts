export type Json = null | boolean | number | string | readonly Json[] | {
    readonly [key: string]: Json;
};
export interface ProgramSource {
    readonly package: string;
    readonly version: string;
    readonly gitCommit: string;
    readonly lockfile: string;
    readonly patches: readonly string[];
    readonly hostAbi: string;
}
export interface OriginStep {
    readonly layer: 'bundle' | 'profile' | 'machine' | 'cli' | 'schema-default';
    readonly sourceUri: string;
    readonly sourceVersion: string;
    readonly sourcePointer: string;
    readonly operation: 'insert' | 'replace' | 'resolve' | 'default';
    readonly resolver?: {
        readonly name: string;
        readonly version: string;
        readonly operationId: string;
        readonly secretVersion?: string;
    };
}
export interface ConfigOrigin {
    readonly entryId: string;
    readonly effectivePointer: string;
    readonly steps: readonly OriginStep[];
}
export function pointerToken(value: string) { return value.replaceAll('~', '~0').replaceAll('/', '~1'); }
function literalScalar(value: unknown): boolean { return value === null || typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value)); }
function literalObject(value: unknown): value is object {
    if (value === null || typeof value !== 'object')
        return false;
    if (Array.isArray(value))
        return true;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
}
function denseArray(value: readonly unknown[], path: string): void {
    for (let i = 0; i < value.length; i++)
        if (!Object.hasOwn(value, i))
            throw TypeError(`SPARSE_LITERAL ${path}/${i}`);
    if (Object.keys(value).length !== value.length)
        throw TypeError(`ARRAY_PROPERTIES ${path}`);
}
export function assertLiteral(value: unknown, path = '', seen = new Set<object>()): asserts value is Json {
    if (literalScalar(value))
        return;
    if (!literalObject(value))
        throw TypeError(`LITERAL_REQUIRED ${path}`);
    if (Array.isArray(value))
        denseArray(value, path);
    if (seen.has(value))
        throw TypeError(`LITERAL_CYCLE ${path}`);
    seen.add(value);
    if (Object.hasOwn(value, '__jsExpr'))
        throw TypeError(`LEGACY_EXPRESSION ${path}/__jsExpr`);
    for (const [key, child] of Object.entries(value))
        assertLiteral(child, `${path}/${pointerToken(key)}`, seen);
    seen.delete(value);
}
export function leaves(value: Json, path = ''): Map<string, Json> {
    const result = new Map<string, Json>();
    if (value !== null && typeof value === 'object' && Object.keys(value).length) {
        for (const [key, item] of Object.entries(value))
            for (const [p, v] of leaves(item, `${path}/${pointerToken(key)}`))
                result.set(p, v);
    }
    else
        result.set(path, value);
    return result;
}
export function freezeJson<T extends Json>(value: T): T {
    if (value && typeof value === 'object') {
        for (const child of Object.values(value))
            freezeJson(child);
        Object.freeze(value);
    }
    return value;
}
export function redact(value: Json, secretPointers: readonly string[], path = ''): Json {
    if (secretPointers.some(secret => path === secret || path.startsWith(secret + '/')))
        return '[REDACTED]';
    if (Array.isArray(value))
        return value.map((v, i) => redact(v, secretPointers, `${path}/${i}`));
    if (value && typeof value === 'object')
        return Object.fromEntries(Object.entries(value).map(([key, v]) => [key, redact(v, secretPointers, `${path}/${pointerToken(key)}`)]));
    return value;
}
