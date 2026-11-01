import type { Json } from './types.js';

type S = { type?: string; dict?: Record<string, S>; inner?: S; list?: S[] } & ((value: unknown) => unknown);
type Handler = (schema: S, value: object, path: string) => string[];

/** 按 Schema 结构列出输入中不被声明接受的键（JSON Pointer）。dict/any/const/原始类型放行；union 取能解析且未知键最少的分支。 */
export function unknownConfigPaths(schema: S, value: Json, path = ''): string[] {
    if (value === null || typeof value !== 'object')
        return [];
    const handler = handlers[schema.type ?? ''];
    return handler ? handler(schema, value, path) : schema.inner ? unknownConfigPaths(schema.inner, value, path) : [];
}
function objectPaths(schema: S, value: object, path: string): string[] {
    const dict = schema.dict ?? {};
    return Object.entries(value).flatMap(([key, item]) => Object.hasOwn(dict, key) ? unknownConfigPaths(dict[key]!, item as Json, path + '/' + escape(key)) : [path + '/' + escape(key)]);
}
function arrayPaths(schema: S, value: object, path: string): string[] {
    return Array.isArray(value) ? value.flatMap((item, index) => unknownConfigPaths(schema.inner!, item, path + '/' + index)) : [];
}
function tuplePaths(schema: S, value: object, path: string): string[] {
    return Array.isArray(value) ? value.flatMap((item, index) => schema.list?.[index] ? unknownConfigPaths(schema.list[index]!, item, path + '/' + index) : [path + '/' + index]) : [];
}
function dictPaths(schema: S, value: object, path: string): string[] {
    return Object.entries(value).flatMap(([key, item]) => unknownConfigPaths(schema.inner!, item as Json, path + '/' + escape(key)));
}
function unionPaths(schema: S, value: Json, path: string): string[] {
    const fits = (schema.list ?? []).filter(branch => { try { branch(structuredClone(value)); return true; } catch { return false; } }).map(branch => unknownConfigPaths(branch, value, path));
    return fits.length ? fits.reduce((best, current) => current.length < best.length ? current : best) : [];
}
function intersectPaths(schema: S, value: object, path: string): string[] {
    const list = schema.list ?? [];
    const keys = new Set(list.flatMap(branch => branch.type === 'object' ? Object.keys(branch.dict ?? {}) : []));
    const open = list.some(branch => branch.type !== 'object');
    const extra = open ? [] : Object.keys(value).filter(key => !keys.has(key)).map(key => path + '/' + escape(key));
    const nested = list.flatMap(branch => branch.type === 'object' ? unknownConfigPaths(branch, Object.fromEntries(Object.entries(value).filter(([key]) => Object.hasOwn(branch.dict ?? {}, key))) as Json, path) : []);
    return [...extra, ...nested];
}
const handlers: Record<string, Handler> = { object: objectPaths, array: arrayPaths, tuple: tuplePaths, dict: dictPaths, union: unionPaths as Handler, intersect: intersectPaths };
const escape = (key: string) => key.replace(/~/g, '~0').replace(/\//g, '~1');
