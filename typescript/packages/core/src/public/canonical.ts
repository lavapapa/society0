/** 规范 JSON 行编码：写入时定型，渲染只是按序拼接。 */
import { compareCodePoints } from '@society0/host/order';
import { posix } from 'node:path';
import { fault } from '../sqlite/contracts.js';
import type { Json } from '../contracts/common.js';
import type { PublicTableDecl } from '../contracts/public.js';

const DECIMAL = /^-?\d+(?:\.\d+)?$/;

export function assertWellFormed(text: string): string {
    if (!text.isWellFormed())
        throw fault('PUBLIC_TEXT', 'text contains a lone surrogate');
    return text;
}
/** 嵌套对象的键按 code point 排序；非整数 number 一律拒绝。 */
export function canonicalJson(value: unknown): Json {
    if (value === null || typeof value === 'boolean')
        return value;
    if (typeof value === 'string')
        return assertWellFormed(value);
    if (typeof value === 'number') {
        if (!Number.isSafeInteger(value))
            throw fault('PUBLIC_FLOAT', String(value));
        return value;
    }
    if (Array.isArray(value))
        return value.map(canonicalJson);
    if (typeof value === 'object') {
        const source = value as Record<string, unknown>;
        const out: Record<string, Json> = {};
        for (const key of Object.keys(source).sort(compareCodePoints))
            out[assertWellFormed(key)] = canonicalJson(source[key]);
        return out;
    }
    throw fault('PUBLIC_VALUE', typeof value);
}
/** 十进制字符串规范化：去前导零、保留给定小数位、禁止指数与空白。 */
export function normalizeDecimal(value: string): string {
    if (!DECIMAL.test(value))
        throw fault('PUBLIC_DECIMAL', value);
    const negative = value.startsWith('-');
    const digits = negative ? value.slice(1) : value;
    const dot = digits.indexOf('.');
    const integer = (dot < 0 ? digits : digits.slice(0, dot)).replace(/^0+(?=\d)/, '');
    const fraction = dot < 0 ? '' : digits.slice(dot + 1);
    if (integer === '0' && (fraction === '' || /^0+$/.test(fraction)))
        return '0';
    return (negative ? '-' : '') + integer + (dot < 0 ? '' : '.' + fraction);
}
function properties(decl: PublicTableDecl): Record<string, Json> {
    const schema = decl.schema as { readonly properties?: Record<string, Json> };
    if (!schema || typeof schema !== 'object' || !schema.properties || Array.isArray(schema.properties))
        throw fault('PUBLIC_SCHEMA', decl.path);
    return schema.properties;
}
function scalar(field: string, value: unknown): Json {
    if (value === undefined || value === null)
        return null;
    if (typeof value === 'string')
        return assertWellFormed(value);
    if (typeof value === 'boolean')
        return value;
    if (typeof value === 'number') {
        if (!Number.isSafeInteger(value))
            throw fault('PUBLIC_FLOAT', field);
        return value;
    }
    throw fault('PUBLIC_FIELD_TYPE', field);
}
function field(decl: PublicTableDecl, name: string, value: unknown): Json {
    if (value === undefined || value === null)
        return null;
    if (decl.decimals?.includes(name)) {
        if (typeof value === 'number')
            throw fault('PUBLIC_FLOAT', name);
        if (typeof value !== 'string')
            throw fault('PUBLIC_DECIMAL', name);
        return normalizeDecimal(value);
    }
    if (value !== null && typeof value === 'object')
        return canonicalJson(value);
    return scalar(name, value);
}
/** 按 schema.properties 声明顺序输出，字段齐全、缺失补 null、未知字段拒绝。 */
export function canonicalRow(decl: PublicTableDecl, row: Json): Record<string, Json> {
    if (row === null || typeof row !== 'object' || Array.isArray(row))
        throw fault('PUBLIC_ROW', decl.path);
    const declared = properties(decl);
    for (const key of Object.keys(row)) {
        if (!Object.hasOwn(declared, key))
            throw fault('PUBLIC_FIELD_UNKNOWN', key);
        assertWellFormed(key);
    }
    const out: Record<string, Json> = {};
    for (const name of Object.keys(declared))
        out[name] = field(decl, name, (row as Record<string, Json>)[name]);
    return out;
}
export function canonicalLine(decl: PublicTableDecl, row: Json): string {
    return JSON.stringify(canonicalRow(decl, row));
}
export function rowKey(decl: PublicTableDecl, row: Json): string {
    const declared = properties(decl);
    return JSON.stringify(decl.key.map(name => {
        if (!Object.hasOwn(declared, name))
            throw fault('PUBLIC_KEY', name);
        const value = scalar(name, (row as Record<string, Json>)[name]);
        if (value === null || typeof value === 'object')
            throw fault('PUBLIC_KEY', name);
        return value;
    }));
}
function pad(value: bigint): string {
    return (value + (1n << 63n)).toString().padStart(20, '0');
}
function sortComponent(value: Json, descending: boolean): Buffer {
    let bytes: Buffer;
    if (value === null)
        bytes = Buffer.from([0]);
    else if (typeof value === 'boolean')
        bytes = Buffer.from([1, value ? 1 : 0]);
    else if (typeof value === 'number')
        bytes = Buffer.from([2, ...Buffer.from(pad(BigInt(value)))]);
    else if (typeof value === 'string')
        bytes = Buffer.concat([Buffer.from([3]), Buffer.from(JSON.stringify(value).slice(1, -1), 'utf8')]);
    else
        bytes = Buffer.concat([Buffer.from([4]), Buffer.from(JSON.stringify(value), 'utf8')]);
    if (descending)
        bytes = Buffer.from(bytes.map(byte => 0xff - byte));
    return bytes;
}
/** 定长编码：字典序即业务序；降序按字节取补。十六进制TEXT便于存储与比较。 */
export function sortKey(decl: PublicTableDecl, row: Json): string {
    return decl.sort.map(({ field: name, order }) => {
        if (!Object.hasOwn(properties(decl), name))
            throw fault('PUBLIC_SORT', name);
        return sortComponent(field(decl, name, (row as Record<string, Json>)[name]), order === 'desc').toString('hex');
    }).join('00');
}
export function shardFile(decl: PublicTableDecl, row: Json): string {
    const spec = decl.shard;
    const value = spec.format === 'none' ? '' : (row as Record<string, Json>)[spec.by];
    if (spec.format !== 'none' && typeof value !== 'string' && typeof value !== 'number')
        throw fault('PUBLIC_SHARD', spec.by);
    return shardFileForValue(decl, String(value ?? ''));
}
export function shardFileForValue(decl: PublicTableDecl, value: string): string {
    const spec = decl.shard;
    let name: string;
    if (spec.file)
        name = spec.file(value);
    else if (spec.format === 'day')
        name = value.slice(0, 10);
    else if (spec.format === 'month')
        name = value.slice(0, 7);
    else if (spec.format === 'year')
        name = value.slice(0, 4);
    else if (spec.format === 'value')
        name = encodeURIComponent(value);
    else
        name = posix.basename(decl.path);
    if (!name || name.includes('/') || name === '.' || name === '..')
        throw fault('PUBLIC_SHARD_NAME', name);
    return `${decl.path}/${name}.jsonl`;
}
export function validateTableDecl(decl: PublicTableDecl): PublicTableDecl {
    if (typeof decl.path !== 'string' || !decl.path.startsWith('/') || decl.path.endsWith('/') || posix.normalize(decl.path) !== decl.path)
        throw fault('PUBLIC_TABLE_PATH', String(decl.path));
    const declared = properties(decl);
    if (!decl.key.length)
        throw fault('PUBLIC_KEY', decl.path);
    for (const name of [...decl.key, ...decl.sort.map(item => item.field), ...(decl.decimals ?? []), decl.shard.by])
        if (name && !Object.hasOwn(declared, name))
            throw fault('PUBLIC_FIELD_UNKNOWN', name);
    if (decl.shard.format === 'none' && decl.shard.by)
        throw fault('PUBLIC_SHARD', decl.path);
    if (decl.shard.format !== 'none' && !decl.shard.by)
        throw fault('PUBLIC_SHARD', decl.path);
    return decl;
}
export function audienceOf(value: unknown): string {
    if (typeof value !== 'string' || !value)
        throw fault('PUBLIC_AUDIENCE', String(value));
    return assertWellFormed(value);
}
