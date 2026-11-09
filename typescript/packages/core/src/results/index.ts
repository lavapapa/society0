import { ResultWriter } from './writer.js';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ batchRows: Schema.natural().min(1).default(64), batchBytes: Schema.natural().min(1).default(1048576) });
export const metadata: PluginMetadata = { "title": "results", "summary": "持久结果集、稳定前缀分页和当前汇总；按行数及字节分批写入。", "configVersion": 1, "configPolicy": [{ "path": "/batchRows", "mode": "boundary", "affectsResults": true, "secret": false }, { "path": "/batchBytes", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "run", "allowedScopes": ["run"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./results", "provides": [{ "key": "results", "protocol": "results", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import type { SqliteMigration, SqliteReadView, SqliteTransaction, TransactionRequest } from '../sqlite/contracts.js';
import type { WriteLease } from '../execution-fence/index.js';
import type { Json } from '../contracts/common.js';
import type { StoredValue, ResultWriteBounds, PayloadRef, PayloadRange, PageRequest, ResultSetDescription } from '../contracts/data.js';
import { attachValue, readValue } from './payload.js';
export { prepareValue, attachValue, readValue, jsonByteBound, textChunks } from './payload.js';
import { after, cursor, page, sized, fail, bytes, bounds as pageBounds, sqlPageBudget, assertSqlPagePrefix } from '../information/page.js';
import '../sqlite/index.js';
import '../artifacts-local/index.js';
export function resultsMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return { owner: 'results', from: 0, to: 1, after: ['artifacts-local'], source, statements: [{ sql: `CREATE TABLE result_sets(id TEXT PRIMARY KEY NOT NULL,head INTEGER NOT NULL,finished INTEGER NOT NULL);
CREATE TABLE result_records(set_id TEXT NOT NULL REFERENCES result_sets(id),ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(set_id,ordinal));
CREATE TABLE result_current(name TEXT PRIMARY KEY NOT NULL,payload TEXT NOT NULL);` }] };
}
export const migrations = (source: Readonly<Record<string, unknown>>) => [resultsMigration(source)];
declare module '@society0/cordis' {
    interface Context {
        results: Results;
    }
}
export class Results extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts'];
    constructor(ctx: Context, private options: {
        batchRows: number;
        batchBytes: number;
    }) { super(ctx, 'results'); }
    write(lease: WriteLease, operationId: string, id: string, rows: AsyncIterable<Json>, limits: ResultWriteBounds, signal: AbortSignal) { return new ResultWriter(this.ctx, lease, operationId, id, limits, signal, this.options).write(rows); }
    async describe(id: string, view: SqliteReadView): Promise<ResultSetDescription> {
        const [row] = await view.query({ sql: 'SELECT head,finished FROM result_sets WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: 4096 });
        if (!row)
            return fail('RESULT_MISSING');
        return { id, through: String(row.head), records: String(row.head), finished: row.finished === 1n || row.finished === 1 };
    }
    /** 两种结果表共享SQL字节前缀，额外候选行只判断后续页。 */
    private async records(view: SqliteReadView, request: PageRequest, sql: string, parameters: readonly (string | bigint)[]) {
        const { limit, budget, rowReserve, nativeBounds } = sqlPageBudget(request);
        const rows = await view.query({ sql: `WITH candidates AS (
SELECT position,payload,sum(length(CAST(json_object('position',position,'payload',payload) AS BLOB))+${rowReserve}) OVER (ORDER BY position ROWS UNBOUNDED PRECEDING) AS used,row_number() OVER (ORDER BY position) AS ordinal
FROM (${sql} ORDER BY position LIMIT ?)
)
SELECT CASE WHEN used<=? THEN position END AS position,CASE WHEN used<=? THEN payload END AS payload,ordinal<(SELECT count(*) FROM candidates) AS has_more
FROM candidates WHERE (ordinal<=? AND used<=?) OR ordinal=1 ORDER BY ordinal`, parameters: [...parameters, limit + 1, budget, budget, limit, budget] }, nativeBounds);
        assertSqlPagePrefix(rows);
        return { rows, more: !!rows.at(-1)?.has_more };
    }
    async page(id: string, view: SqliteReadView, request: PageRequest & {
        through?: string;
    }) {
        pageBounds(request);
        const description = await this.describe(id, view);
        let through = request.through;
        if (request.cursor && !through) {
            let decoded;
            try {
                decoded = JSON.parse(Buffer.from(request.cursor, 'base64url').toString());
            }
            catch {
                return fail('CURSOR_INVALID');
            }
            through = decoded.identity.through;
        }
        through ??= description.through;
        if (!/^\d+$/.test(through) || BigInt(through) > BigInt(description.through))
            return fail('RESULT_THROUGH');
        const identity = { runId: view.runId, id, through };
        const last = after(request.cursor, identity) ?? '0';
        const selected = await this.records(view, request, 'SELECT ordinal AS position,payload FROM result_records WHERE set_id=? AND ordinal>? AND ordinal<=?', [id, BigInt(last), BigInt(through)]);
        const items = selected.rows.map(row => ({ ordinal: String(row.position), value: JSON.parse(String(row.payload)) as StoredValue }));
        const result = page(items, through, view.revision, { ...request, maxResponseBytes: request.maxResponseBytes - 100 }, identity, x => x.ordinal, selected.more);
        return sized({ ...result, through, finished: description.finished && through === description.through, responseBytes: 0 }, request.maxResponseBytes);
    }
    async current(view: SqliteReadView, request: PageRequest) {
        pageBounds(request);
        const identity = { runId: view.runId, revision: view.revision, kind: 'current' }, last = after(request.cursor, identity) ?? '';
        const [count] = await view.query({ sql: 'SELECT count(*) AS total FROM result_current' }, { maxRows: 1, maxBytes: 1024 });
        const selected = await this.records(view, request, 'SELECT name AS position,payload FROM result_current WHERE name>?', [last]);
        return page(selected.rows.map(row => ({ name: String(row.position), value: JSON.parse(String(row.payload)) as StoredValue })), String(count!.total), view.revision, request, identity, x => x.name, selected.more);
    }
    async setCurrent(tx: SqliteTransaction, name: string, value: StoredValue) {
        if (bytes(value) > 16384)
            return fail('RESULT_INLINE_BUDGET');
        await attachValue(tx, value, this.ctx.artifacts);
        await tx.execute({ sql: 'INSERT INTO result_current VALUES(?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload', parameters: [name, JSON.stringify(value)] });
    }
    readPayload(ref: PayloadRef, range: PayloadRange, signal?: AbortSignal) { return readValue(ref, range, this.ctx.artifacts, signal); }
}
export default Results;
