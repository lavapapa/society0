/** 变更日志：按修订列与受众聚合 (from,to] 内的变更，代价与变更量成正比。 */
import { compareCodePoints } from '@society0/host/order';
import type { Page, PageRequest } from '../contracts/data.js';
import type { Json } from '../contracts/common.js';
import type { Audience, PublicChange, PublicChangeRows, PublicDocDecl, PublicFile, PublicNode, PublicNotify, PublicTableDecl } from '../contracts/public.js';
import type { PerceptionScope, PerceptionSource, PerceptionPage } from '../contracts/perception.js';
import { fault } from '../sqlite/contracts.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import { sized } from '../information/page.js';
import { readFile, readNode, revisionNumber } from './store.js';
import { intersects } from './audience.js';

export const CHANGE_FILES_SQL = `SELECT file FROM public_files WHERE rev_from>? AND rev_from<=?
UNION
SELECT file FROM public_files WHERE rev_to>? AND rev_to<=?
ORDER BY file`;
export const CHANGE_DOCS_SQL = `SELECT path FROM public_nodes WHERE kind='doc' AND rev_from>? AND rev_from<=?
UNION
SELECT path FROM public_nodes WHERE kind='doc' AND rev_to>? AND rev_to<=?
ORDER BY path`;
/** 供 EXPLAIN 断言使用；真实查询按受众数量生成同样的占位符。 */
export const CHANGE_ROWS_SQL = `WITH closed AS (
  SELECT DISTINCT row_key FROM public_rows WHERE file=? AND rev_to>? AND rev_to<=? AND (audience='all' OR audience IN (?))
)
SELECT (SELECT count(*) FROM closed) AS closed,
  (SELECT count(*) FROM closed c WHERE EXISTS (
    SELECT 1 FROM public_rows o WHERE o.file=? AND o.row_key=c.row_key AND o.rev_from>? AND o.rev_from<=? AND (audience='all' OR audience IN (?))
  )) AS updated`;

export interface PublicChangeEntry {
    readonly change: PublicChange;
    readonly notify: PublicNotify;
}
export interface PublicChangeReader {
    readonly tables: ReadonlyMap<string, PublicTableDecl>;
    readonly docs: ReadonlyMap<string, PublicDocDecl>;
    readonly defaultNotify: 'digest' | 'percept';
    readonly limits: { readonly maxQueryBytes: number; readonly maxQueryRows: number };
}
const COUNT_BOUNDS = { maxRows: 1, maxBytes: 4096 };

function audienceClause(audiences: readonly Audience[]): { sql: string; parameters: readonly string[] } {
    if (!audiences.length)
        return { sql: '0', parameters: [] };
    return { sql: `(audience='all' OR audience IN (${audiences.map(() => '?').join(',')}))`, parameters: audiences };
}
function rowsSql(audiences: readonly Audience[]): string {
    const clause = audienceClause(audiences).sql;
    return `WITH closed AS (
  SELECT DISTINCT row_key FROM public_rows WHERE file=? AND rev_to>? AND rev_to<=? AND ${clause}
)
SELECT (SELECT count(*) FROM closed) AS closed,
  (SELECT count(*) FROM closed c WHERE EXISTS (
    SELECT 1 FROM public_rows o WHERE o.file=? AND o.row_key=c.row_key AND o.rev_from>? AND o.rev_from<=? AND ${clause}
  )) AS updated`;
}
function notifyMode(reader: PublicChangeReader, path: string, tablePath?: string): PublicNotify {
    const declared = tablePath
        ? reader.tables.get(tablePath)?.notify ?? [...reader.tables.values()].find(decl => decl.path === tablePath)?.notify
        : reader.docs.get(path)?.notify;
    return declared ?? reader.defaultNotify;
}
function audienceOk(audience: Audience, agent: readonly Audience[]): boolean { return audience === 'all' || agent.includes(audience); }
function text(value: unknown): string {
    if (typeof value !== 'string')
        throw fault('PUBLIC_ROW', 'change column');
    return value;
}
function count(value: unknown): number { return typeof value === 'bigint' ? Number(value) : Number(value ?? 0); }
function canonical(from: string, to: string): { from: bigint; to: bigint } {
    const a = revisionNumber(from), b = revisionNumber(to);
    if (a > b)
        throw fault('PUBLIC_REVISION', `${from}>${to}`);
    return { from: a, to: b };
}
function cursor(from: string, to: string, after: string): string {
    return Buffer.from(JSON.stringify({ from, to, after })).toString('base64url');
}
function after(token: string | undefined, from: string, to: string): string | undefined {
    if (!token)
        return undefined;
    try {
        const parsed = JSON.parse(Buffer.from(token, 'base64url').toString()) as { from?: string; to?: string; after?: string };
        if (parsed.from !== from || parsed.to !== to || typeof parsed.after !== 'string')
            throw Error('cursor');
        return parsed.after;
    }
    catch {
        throw fault('CURSOR_STALE');
    }
}
async function candidates(view: SqliteReadView, sql: string, column: 'file' | 'path', from: bigint, to: bigint, limits: PublicChangeReader['limits']): Promise<readonly string[]> {
    const found: string[] = [];
    let cursor = '';
    while (true) {
        const rows = await view.query({ sql: `SELECT ${column} FROM (${sql}) WHERE ${column}>? ORDER BY ${column} LIMIT ?`, parameters: [from, to, from, to, cursor, limits.maxQueryRows] }, { maxRows: limits.maxQueryRows, maxBytes: limits.maxQueryBytes });
        for (const row of rows)
            found.push(text(row.file ?? row.path));
        if (rows.length < limits.maxQueryRows)
            break;
        cursor = found[found.length - 1]!;
    }
    return found;
}
async function visibleRows(view: SqliteReadView, file: string, revision: string, audiences: readonly Audience[]): Promise<number> {
    const at = revisionNumber(revision), audience = audienceClause(audiences);
    const [row] = await view.query({ sql: `SELECT count(*) AS n FROM public_rows WHERE file=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) AND ${audience.sql}`, parameters: [file, at, at, ...audience.parameters] }, COUNT_BOUNDS);
    return count(row?.n);
}
async function rowCounts(view: SqliteReadView, file: string, from: bigint, to: bigint, beforeRows: number, afterRows: number, audiences: readonly Audience[]): Promise<PublicChangeRows> {
    const audience = audienceClause(audiences);
    const [row] = await view.query({ sql: rowsSql(audiences), parameters: [file, from, to, ...audience.parameters, file, from, to, ...audience.parameters] }, COUNT_BOUNDS);
    const closed = count(row?.closed), updated = count(row?.updated), retracted = Math.max(0, closed - updated);
    return { added: Math.max(0, afterRows - beforeRows + retracted), updated, retracted };
}
function fileKind(a: PublicFile | null, b: PublicFile | null): PublicChange['kind'] | undefined {
    if (!a && !b)
        return undefined;
    if (!a)
        return 'added';
    if (!b)
        return 'retracted';
    return 'updated';
}
function docKind(a: PublicNode | null, b: PublicNode | null): PublicChange['kind'] | undefined {
    if (!a && !b)
        return undefined;
    if (!a)
        return 'added';
    if (!b)
        return 'retracted';
    return 'updated';
}
function summarize(items: readonly PublicChange[]): Json {
    return items.map(change => ({
        path: change.path,
        kind: change.kind,
        ...(change.rows ? { rows: change.rows as unknown as Json } : {}),
        notify: change.notify
    }));
}
function textSummary(items: readonly PublicChange[], from: string, to: string): string {
    const lines = items.map(change => {
        const rows = change.rows;
        if (!rows)
            return `${change.path} ${change.kind === 'added' ? '新文档' : change.kind === 'retracted' ? '撤下文档' : '更新文档'}`;
        const parts: string[] = [];
        if (rows.added)
            parts.push(`新增 ${rows.added} 行`);
        if (rows.updated)
            parts.push(`更新 ${rows.updated} 行`);
        if (rows.retracted)
            parts.push(`撤下 ${rows.retracted} 行`);
        return `${change.path} ${parts.join('；') || '变更'}`;
    });
    return `[public.changes] (${from}, ${to}]\n${lines.join('\n')}`;
}

/** 读取变更条目；调用方负责快照与受众解析。 */
export async function changeEntries(view: SqliteReadView, _scope: { readonly actorId: string; readonly momentId: string }, from: string, to: string, reader: PublicChangeReader, agent: readonly Audience[]): Promise<readonly PublicChangeEntry[]> {
    const bounds = canonical(from, to);
    if (bounds.from === bounds.to)
        return [];
    const files = await candidates(view, CHANGE_FILES_SQL, 'file', bounds.from, bounds.to, reader.limits);
    const docs = await candidates(view, CHANGE_DOCS_SQL, 'path', bounds.from, bounds.to, reader.limits);
    const entries: PublicChangeEntry[] = [];
    for (const file of files) {
        const before = await readFile(view, file, from), after = await readFile(view, file, to);
        const kind = fileKind(before, after);
        if (!kind)
            continue;
        if (!(before && intersects(before.audiences, agent)) && !(after && intersects(after.audiences, agent)))
            continue;
        const tablePath = after?.tablePath ?? before?.tablePath;
        const mode = notifyMode(reader, file, tablePath);
        const revision = after?.revision ?? before!.revision;
        const beforeRows = before ? (before.audiences.length === 1 && before.audiences[0] === 'all' ? before.rows : await visibleRows(view, file, from, agent)) : 0;
        const afterRows = after ? (after.audiences.length === 1 && after.audiences[0] === 'all' ? after.rows : await visibleRows(view, file, to, agent)) : 0;
        const rows = await rowCounts(view, file, bounds.from, bounds.to, beforeRows, afterRows, agent);
        entries.push({ change: { path: file, kind, rows, revision, notify: mode }, notify: mode });
    }
    for (const path of docs) {
        const before = await readNode(view, path, from), after = await readNode(view, path, to);
        const kind = docKind(before, after);
        if (!kind)
            continue;
        if (!(before && audienceOk(before.audience, agent)) && !(after && audienceOk(after.audience, agent)))
            continue;
        const mode = notifyMode(reader, path);
        entries.push({ change: { path, kind, revision: after?.revision ?? before!.revision, notify: mode }, notify: mode });
    }
    entries.sort((a, b) => compareCodePoints(a.change.path, b.change.path));
    return entries;
}

/** Public.changes 的 Page 视图。 */
export async function readChanges(view: SqliteReadView, scope: { readonly actorId: string; readonly momentId: string }, from: string, to: string, reader: PublicChangeReader, agent: readonly Audience[], request: PageRequest): Promise<Page<PublicChange>> {
    const entries = await changeEntries(view, scope, from, to, reader, agent);
    const changes = entries.map(entry => entry.change);
    const offset = after(request.cursor, from, to);
    const start = offset === undefined ? 0 : changes.findIndex(change => compareCodePoints(change.path, offset) > 0);
    const begin = start < 0 ? changes.length : start;
    const selected = changes.slice(begin, begin + request.maxItems);
    const more = begin + selected.length < changes.length;
    return sized({
        items: selected,
        total: String(changes.length),
        ...(more ? { next: cursor(from, to, selected[selected.length - 1]!.path) } : {}),
        revision: view.revision,
        responseBytes: 0
    }, request.maxResponseBytes);
}

export interface PublicChangesSourceHost {
    readonly enabled: boolean;
    revision(scope: PerceptionScope, view: SqliteReadView | SqliteTransaction): Promise<string>;
    request(): PageRequest;
    page(view: SqliteReadView, scope: PerceptionScope, from: string, to: string, request: PageRequest): Promise<Page<PublicChange>>;
}
/** PerceptionSource：游标是上次送达的 R_m，材料只含路径与计数。 */
export function publicChangesSource(host: PublicChangesSourceHost): PerceptionSource {
    return {
        id: 'public.changes',
        schema: { type: 'object', properties: { from: { type: 'string' }, to: { type: 'string' }, changes: { type: 'array' } } },
        cursorScope: 'actor',
        async available() { return host.enabled; },
        async versions(scope, reader) { return [{ owner: 'public.changes', version: await host.revision(scope, reader) }]; },
        async collect(scope, afterCursor, _through, reader): Promise<PerceptionPage> {
            const to = await host.revision(scope, reader);
            const from = afterCursor === null || afterCursor === undefined ? '0' : String(afterCursor);
            const fromAt = revisionNumber(from), toAt = revisionNumber(to);
            if (fromAt >= toAt)
                return { items: [], nextCursor: fromAt > toAt ? from : to, through: to, hasMore: false };
            const changes: PublicChange[] = [];
            let cursor: string | undefined;
            do {
                const page = await host.page(reader, scope, from, to, { ...host.request(), ...(cursor === undefined ? {} : { cursor }) });
                changes.push(...page.items.filter(change => change.notify !== 'none'));
                cursor = page.next;
            } while (cursor !== undefined);
            if (!changes.length)
                return { items: [], nextCursor: to, through: to, hasMore: false };
            const value = {
                kind: 'public.changes',
                from,
                to,
                changes: summarize(changes),
                text: textSummary(changes, from, to)
            } as Json;
            return {
                items: [{
                    id: `public.changes:${from}:${to}`,
                    kind: 'notice',
                    material: { kind: 'inline', value },
                    provenance: {
                        provider: 'public',
                        source: 'public.changes',
                        notify: changes.some(change => change.notify === 'percept') ? 'percept' : 'digest'
                    }
                }],
                nextCursor: to,
                through: to,
                hasMore: false
            };
        }
    };
}
