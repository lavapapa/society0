/** as-of 读取：任一段快照上按 rev_from<=R<rev_to 取同一世界。 */
import { posix } from 'node:path';
import { fault } from '../sqlite/contracts.js';
import type { SqliteReadView, SqliteRow } from '../sqlite/contracts.js';
import type { Json } from '../contracts/common.js';
import type { Audience, PublicFile, PublicNode, PublicRow } from '../contracts/public.js';

export const AS_OF = 'rev_from<=? AND (rev_to IS NULL OR rev_to>?)';
const PAGE_ROWS = 128;

export function revisionNumber(value: string): bigint {
    if (typeof value !== 'string' || !/^\d+$/.test(value))
        throw fault('PUBLIC_REVISION', String(value));
    return BigInt(value);
}
function text(value: unknown, name: string): string {
    if (typeof value !== 'string')
        throw fault('PUBLIC_ROW', name);
    return value;
}
function blob(value: unknown): Uint8Array {
    return value instanceof Uint8Array ? value : new Uint8Array(value as ArrayBufferLike);
}
function audienceClause(audiences: readonly Audience[]): { sql: string; parameters: readonly string[] } {
    if (!audiences.length)
        throw fault('PUBLIC_AUDIENCE', 'empty audience set');
    return { sql: `(audience='all' OR audience IN (${audiences.map(() => '?').join(',')}))`, parameters: audiences };
}
export async function readRows(view: SqliteReadView, file: string, revision: string, audiences: readonly Audience[], request: { readonly maxRows: number; readonly maxBytes: number }): Promise<readonly PublicRow[]> {
    const at = revisionNumber(revision), audience = audienceClause(audiences);
    const rows = await view.query({
        sql: `SELECT row_key,sort_key,line,audience,rev_from FROM public_rows WHERE file=? AND ${AS_OF} AND ${audience.sql} ORDER BY sort_key,row_key LIMIT ?`,
        parameters: [file, at, at, ...audience.parameters, request.maxRows]
    }, { maxRows: request.maxRows, maxBytes: request.maxBytes });
    return rows.map(row => ({
        file, rowKey: text(row.row_key, 'row_key'), sortKey: text(row.sort_key, 'sort_key'),
        revision: String(row.rev_from), audience: text(row.audience, 'audience'), line: blob(row.line)
    }));
}
export async function readFile(view: SqliteReadView, file: string, revision: string): Promise<PublicFile | null> {
    const at = revisionNumber(revision);
    const [row] = await view.query({ sql: `SELECT parent,table_path,rows,bytes,audiences,rev_from FROM public_files WHERE file=? AND ${AS_OF}`, parameters: [file, at, at] }, { maxRows: 1, maxBytes: 65536 });
    if (!row)
        return null;
    return {
        file, parent: text(row.parent, 'parent'), tablePath: text(row.table_path, 'table_path'),
        rows: Number(row.rows), bytes: Number(row.bytes), revision: String(row.rev_from),
        audiences: JSON.parse(text(row.audiences, 'audiences')) as Audience[]
    };
}
export async function readFiles(view: SqliteReadView, parent: string, revision: string, request: { readonly maxRows: number; readonly maxBytes: number }): Promise<readonly PublicFile[]> {
    const at = revisionNumber(revision);
    const rows = await view.query({ sql: `SELECT file,parent,table_path,rows,bytes,audiences,rev_from FROM public_files WHERE parent=? AND ${AS_OF} ORDER BY file LIMIT ?`, parameters: [parent, at, at, request.maxRows] }, { maxRows: request.maxRows, maxBytes: request.maxBytes });
    return rows.map(row => ({
        file: text(row.file, 'file'), parent: text(row.parent, 'parent'), tablePath: text(row.table_path, 'table_path'),
        rows: Number(row.rows), bytes: Number(row.bytes), revision: String(row.rev_from),
        audiences: JSON.parse(text(row.audiences, 'audiences')) as Audience[]
    }));
}
export async function readNode(view: SqliteReadView, path: string, revision: string): Promise<PublicNode | null> {
    const at = revisionNumber(revision);
    const [row] = await view.query({ sql: `SELECT parent,kind,provider,node_id,media,meta,audience,body,artifact,bytes,rev_from FROM public_nodes WHERE path=? AND ${AS_OF}`, parameters: [path, at, at] }, { maxRows: 1, maxBytes: 1048576 });
    if (!row)
        return null;
    return {
        path, parent: text(row.parent, 'parent'), kind: text(row.kind, 'kind') as PublicNode['kind'],
        revision: String(row.rev_from), provider: text(row.provider, 'provider'), nodeId: text(row.node_id, 'node_id'),
        media: text(row.media, 'media'), meta: JSON.parse(text(row.meta, 'meta')) as Json,
        audience: text(row.audience, 'audience'),
        ...(row.body === null || row.body === undefined ? {} : { body: blob(row.body) }),
        ...(typeof row.artifact === 'string' ? { artifact: row.artifact } : {}),
        ...(row.bytes === null || row.bytes === undefined ? {} : { bytes: Number(row.bytes) })
    };
}
export async function readChildren(view: SqliteReadView, parent: string, revision: string, request: { readonly maxRows: number; readonly maxBytes: number; readonly kinds?: readonly string[] }): Promise<readonly PublicNode[]> {
    const at = revisionNumber(revision);
    const kinds = request.kinds && request.kinds.length ? ` AND kind IN (${request.kinds.map(() => '?').join(',')})` : '';
    const rows = await view.query({ sql: `SELECT path,parent,kind,provider,node_id,media,meta,audience,bytes,rev_from FROM public_nodes WHERE parent=? AND ${AS_OF}${kinds} ORDER BY path LIMIT ?`, parameters: [parent, at, at, ...(request.kinds ?? []), request.maxRows] }, { maxRows: request.maxRows, maxBytes: request.maxBytes });
    return rows.map(row => ({
        path: text(row.path, 'path'), parent: text(row.parent, 'parent'), kind: text(row.kind, 'kind') as PublicNode['kind'],
        revision: String(row.rev_from), provider: text(row.provider, 'provider'), nodeId: text(row.node_id, 'node_id'),
        media: text(row.media, 'media'), meta: JSON.parse(text(row.meta, 'meta')) as Json,
        audience: text(row.audience, 'audience'),
        ...(row.bytes === null || row.bytes === undefined ? {} : { bytes: Number(row.bytes) })
    }));
}
/** 渲染即按 (sort_key,row_key) 拼接规范行；空文件为0字节。分页大小由数据库查询预算与文件平均行宽推导。 */
export async function renderRows(view: SqliteReadView, file: string, revision: string, audiences: readonly Audience[], limits?: { readonly maxQueryBytes: number; readonly maxQueryRows: number }, rows?: number, bytes?: number): Promise<Uint8Array> {
    const at = revisionNumber(revision), audience = audienceClause(audiences);
    const average = limits && rows && bytes ? Math.max(1, Math.ceil(bytes / Math.max(1, rows))) + 1 : 0;
    const page = limits && average ? Math.max(1, Math.min(limits.maxQueryRows, Math.floor(limits.maxQueryBytes / average))) : PAGE_ROWS;
    const maxBytes = limits?.maxQueryBytes ?? 1048576;
    const total = rows ?? 0;
    if (limits && total > 0 && total <= page && (bytes ?? 0) <= maxBytes) {
        const [aggregate] = await view.query({
            sql: `SELECT group_concat(line, char(10)) AS data FROM (SELECT line FROM public_rows WHERE file=? AND ${AS_OF} AND ${audience.sql} ORDER BY sort_key,row_key LIMIT ?)`,
            parameters: [file, at, at, ...audience.parameters, total]
        }, { maxRows: 1, maxBytes });
        const data = aggregate?.data === null || aggregate?.data === undefined ? null : aggregate.data;
        return data === null ? new Uint8Array() : Buffer.concat([Buffer.from(data as Uint8Array), Buffer.from('\n')]);
    }
    const chunks: Uint8Array[] = [];
    let cursor: { sort: string; key: string } | undefined;
    while (true) {
        const bounds = cursor ? ' AND (sort_key>? OR (sort_key=? AND row_key>?))' : '';
        const parameters = [file, at, at, ...audience.parameters, ...(cursor ? [cursor.sort, cursor.sort, cursor.key] : []), page];
        const result = await view.query({ sql: `SELECT sort_key,row_key,line FROM public_rows WHERE file=? AND ${AS_OF} AND ${audience.sql}${bounds} ORDER BY sort_key,row_key LIMIT ?`, parameters }, { maxRows: page, maxBytes });
        for (const row of result) {
            chunks.push(blob(row.line));
            chunks.push(Buffer.from('\n'));
        }
        if (result.length < page)
            break;
        const last = result[result.length - 1]!;
        cursor = { sort: text(last.sort_key, 'sort_key'), key: text(last.row_key, 'row_key') };
    }
    return Buffer.concat(chunks);
}
export function parentOf(path: string): string {
    const parent = posix.dirname(path);
    return parent === '.' ? '/' : parent;
}
export type { SqliteRow };
