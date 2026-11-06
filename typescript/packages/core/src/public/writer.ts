/** 写指令：调用方自己的原生短事务内提交，rev=tx.baseRevision+1。 */
import { fault } from '../sqlite/contracts.js';
import type { SqliteTransaction } from '../sqlite/contracts.js';
import type { Json } from '../contracts/common.js';
import type { ArtifactRef } from '../artifacts-local/store.js';
import type { Audience, PublicDocDecl, PublicNode, PublicRowWriteOptions, PublicTableDecl, PublicWriter } from '../contracts/public.js';
import { audienceOf, canonicalJson, canonicalRow, rowKey, shardFile, shardFileForValue, sortKey } from './canonical.js';
import { AS_OF, parentOf } from './store.js';

interface Entry {
    readonly decl: PublicTableDecl;
    readonly line: string;
    readonly file: string;
    readonly key: string;
    readonly sort: string;
    readonly audience: Audience;
}
interface State { readonly rev: bigint; readonly dirty: Map<string, PublicTableDecl>; }
interface NodeSpec {
    readonly kind: PublicNode['kind'];
    readonly parent: string;
    readonly provider: string;
    readonly nodeId: string;
    readonly media: string;
    readonly meta: Json;
    readonly audience: Audience;
    readonly body?: Uint8Array;
    readonly artifact?: string;
    readonly bytes?: number;
}
interface Live { readonly rev: string; readonly sort: string; readonly audience: string; readonly line: Uint8Array }
const states = new WeakMap<SqliteTransaction, State>();
const BATCH = 8192;
export function extensionOf(file: string): string {
    const name = file.slice(file.lastIndexOf('/') + 1), dot = name.lastIndexOf('.');
    return dot > 0 ? name.slice(dot + 1) : '';
}
export function basenameOf(file: string): string { return file.slice(file.lastIndexOf('/') + 1); }
function same(line: Uint8Array, sort: string, audience: string, entry: Entry): boolean {
    return Buffer.from(line).toString('utf8') === entry.line && sort === entry.sort && audience === entry.audience;
}
export class PublicWriterImpl implements PublicWriter {
    private readonly state: State;
    constructor(private tx: SqliteTransaction, private tables: ReadonlyMap<string, PublicTableDecl>, private docs: ReadonlyMap<string, PublicDocDecl>) {
        const existing = states.get(tx);
        if (existing && existing.rev !== BigInt(tx.baseRevision) + 1n)
            throw fault('PUBLIC_REVISION', 'transaction revision changed');
        this.state = existing ?? { rev: BigInt(tx.baseRevision) + 1n, dirty: new Map() };
        states.set(tx, this.state);
    }
    private declaration(table: string): PublicTableDecl {
        const decl = this.tables.get(table);
        if (!decl)
            throw fault('PUBLIC_TABLE_UNKNOWN', table);
        return decl;
    }
    private async liveRows(file: string): Promise<Map<string, Live>> {
        const live = new Map<string, Live>();
        let cursor: string | undefined;
        while (true) {
            const rows = await this.tx.query({
                sql: 'SELECT row_key,rev_from,sort_key,audience,line FROM public_rows WHERE file=? AND rev_to IS NULL' + (cursor === undefined ? '' : ' AND row_key>?') + ' ORDER BY row_key LIMIT 512',
                parameters: cursor === undefined ? [file] : [file, cursor]
            }, { maxRows: 512, maxBytes: 4194304 });
            for (const row of rows)
                live.set(String(row.row_key), { rev: String(row.rev_from), sort: String(row.sort_key), audience: String(row.audience), line: row.line as Uint8Array });
            if (rows.length < 512)
                return live;
            cursor = String(rows[rows.length - 1]!.row_key);
        }
    }
    private async many(sql: string, rows: readonly (readonly (string | bigint | Uint8Array | null)[])[]): Promise<void> {
        for (let offset = 0; offset < rows.length; offset += 256) {
            const batch = rows.slice(offset, offset + 256);
            await this.tx.executeMany(sql, batch, { maxRows: batch.length, maxInputBytes: 524288 });
        }
    }
    /** 批量diff：关旧、插新、同事务内原地改写；每个文件只聚合一次。 */
    private async apply(entries: readonly Entry[]): Promise<number> {
        const files = new Map<string, Entry[]>();
        for (const entry of entries) {
            const list = files.get(entry.file);
            if (list)
                list.push(entry);
            else
                files.set(entry.file, [entry]);
        }
        let changed = 0;
        for (const [file, list] of files) {
            const live = await this.liveRows(file);
            const closes: (readonly (string | bigint)[])[] = [], inserts: (readonly (string | bigint | Uint8Array | null)[])[] = [], updates: (readonly (string | bigint | Uint8Array | null)[])[] = [];
            for (const entry of list) {
                const current = live.get(entry.key);
                if (!current) {
                    inserts.push([entry.file, entry.key, entry.sort, this.state.rev, entry.audience, Buffer.from(entry.line, 'utf8')]);
                    changed++;
                }
                else if (current.rev === String(this.state.rev)) {
                    if (same(current.line, current.sort, entry.audience, entry))
                        continue;
                    updates.push([entry.sort, entry.audience, Buffer.from(entry.line, 'utf8'), entry.file, entry.key, this.state.rev]);
                    changed++;
                }
                else if (!same(current.line, current.sort, entry.audience, entry)) {
                    closes.push([this.state.rev, entry.file, entry.key]);
                    inserts.push([entry.file, entry.key, entry.sort, this.state.rev, entry.audience, Buffer.from(entry.line, 'utf8')]);
                    changed++;
                }
            }
            if (closes.length)
                await this.many('UPDATE public_rows SET rev_to=? WHERE file=? AND row_key=? AND rev_to IS NULL', closes);
            if (inserts.length)
                await this.many('INSERT INTO public_rows(file,row_key,sort_key,rev_from,rev_to,audience,line) VALUES(?,?,?,?,NULL,?,?)', inserts);
            if (updates.length)
                await this.many('UPDATE public_rows SET sort_key=?,audience=?,line=? WHERE file=? AND row_key=? AND rev_from=?', updates);
            this.state.dirty.set(file, list[0]!.decl);
            changed += await this.aggregate(file, list[0]!.decl);
        }
        return changed;
    }
    private async aggregate(file: string, decl: PublicTableDecl): Promise<number> {
        const at = this.state.rev;
        const [agg] = await this.tx.query({ sql: `SELECT count(*) AS n,coalesce(sum(length(line)+1),0) AS bytes FROM public_rows WHERE file=? AND ${AS_OF}`, parameters: [file, at, at] }, { maxRows: 1, maxBytes: 4096 });
        const rows = Number(agg?.n ?? 0), bytes = Number(agg?.bytes ?? 0);
        const audienceRows = await this.tx.query({ sql: `SELECT DISTINCT audience FROM public_rows WHERE file=? AND ${AS_OF} ORDER BY audience`, parameters: [file, at, at] }, { maxRows: 4096, maxBytes: 262144 });
        const audiences = JSON.stringify(audienceRows.map(row => String(row.audience)));
        const [current] = await this.tx.query({ sql: 'SELECT rowid AS id,rev_from FROM public_files WHERE file=? AND rev_to IS NULL', parameters: [file] }, { maxRows: 1, maxBytes: 4096 });
        if (!rows) {
            if (!current)
                return 0;
            if (String(current.rev_from) === String(at))
                await this.tx.execute({ sql: 'DELETE FROM public_files WHERE rowid=?', parameters: [current.id as bigint] });
            else
                await this.tx.execute({ sql: 'UPDATE public_files SET rev_to=? WHERE rowid=?', parameters: [at, current.id as bigint] });
            return 0;
        }
        if (current && String(current.rev_from) === String(at))
            await this.tx.execute({ sql: 'UPDATE public_files SET rows=?,bytes=?,audiences=? WHERE rowid=?', parameters: [rows, bytes, audiences, current.id as bigint] });
        else {
            if (current)
                await this.tx.execute({ sql: 'UPDATE public_files SET rev_to=? WHERE rowid=?', parameters: [at, current.id as bigint] });
            await this.tx.execute({ sql: 'INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,NULL,?,?,?,?,?,?)', parameters: [file, decl.path, at, decl.path, rows, bytes, audiences, basenameOf(file), extensionOf(file)] });
        }
        await this.ensureDir(decl);
        await this.versionNode(file, { kind: 'table', parent: decl.path, provider: 'public', nodeId: decl.path, media: 'application/jsonl', meta: canonicalJson({ table: decl.path, rows, bytes }), audience: 'all', bytes });
        return 0;
    }
    private async ensureDir(decl: PublicTableDecl): Promise<void> {
        const [live] = await this.tx.query({ sql: 'SELECT path FROM public_nodes WHERE path=? AND rev_to IS NULL', parameters: [decl.path] }, { maxRows: 1, maxBytes: 1024 });
        if (!live)
            await this.insertNode(decl.path, { kind: 'dir', parent: parentOf(decl.path), provider: 'public', nodeId: decl.path, media: 'application/json', meta: canonicalJson({ title: decl.title, description: decl.description }), audience: 'all' });
    }
    private async insertNode(path: string, spec: NodeSpec): Promise<void> {
        await this.tx.execute({ sql: 'INSERT INTO public_nodes(path,parent,rev_from,rev_to,kind,provider,node_id,media,meta,audience,body,artifact,bytes) VALUES(?,?,?,NULL,?,?,?,?,?,?,?,?,?)', parameters: [path, spec.parent, this.state.rev, spec.kind, spec.provider, spec.nodeId, spec.media, JSON.stringify(spec.meta), spec.audience, spec.body ?? null, spec.artifact ?? null, spec.bytes ?? null] });
    }
    private async versionNode(path: string, spec: NodeSpec): Promise<void> {
        const [live] = await this.tx.query({ sql: 'SELECT rowid AS id,rev_from,kind,parent,provider,node_id,media,meta,audience,body,artifact,bytes FROM public_nodes WHERE path=? AND rev_to IS NULL', parameters: [path] }, { maxRows: 1, maxBytes: 8388608 });
        if (!live) {
            await this.insertNode(path, spec);
            return;
        }
        const unchanged = live.kind === spec.kind && live.parent === spec.parent && live.provider === spec.provider && live.node_id === spec.nodeId
            && live.media === spec.media && live.audience === spec.audience && live.meta === JSON.stringify(spec.meta)
            && (live.body === null || live.body === undefined ? spec.body === undefined : spec.body !== undefined && Buffer.compare(live.body as Uint8Array, spec.body) === 0)
            && (live.artifact ?? undefined) === spec.artifact
            && Number(live.bytes ?? 0) === Number(spec.bytes ?? 0);
        if (unchanged)
            return;
        if (String(live.rev_from) === String(this.state.rev)) {
            await this.tx.execute({ sql: 'UPDATE public_nodes SET parent=?,kind=?,provider=?,node_id=?,media=?,meta=?,audience=?,body=?,artifact=?,bytes=? WHERE rowid=?', parameters: [spec.parent, spec.kind, spec.provider, spec.nodeId, spec.media, JSON.stringify(spec.meta), spec.audience, spec.body ?? null, spec.artifact ?? null, spec.bytes ?? null, live.id as bigint] });
            return;
        }
        await this.tx.execute({ sql: 'UPDATE public_nodes SET rev_to=? WHERE rowid=?', parameters: [this.state.rev, live.id as bigint] });
        await this.insertNode(path, spec);
    }
    async upsert(table: string, rows: Iterable<Json>, options?: PublicRowWriteOptions): Promise<{ changed: number }> {
        const decl = this.declaration(table);
        const last = new Map<string, Entry>();
        for (const row of rows) {
            const canonical = canonicalRow(decl, row);
            const entry: Entry = {
                decl, line: JSON.stringify(canonical), file: shardFile(decl, canonical), key: rowKey(decl, canonical),
                sort: sortKey(decl, canonical), audience: this.audience(options?.audience, canonical)
            };
            last.set(entry.file + '\u0000' + entry.key, entry);
        }
        const changed = await this.apply([...last.values()]);
        return { changed };
    }
    private audience(option: PublicRowWriteOptions['audience'], row: Json): Audience {
        if (typeof option === 'function')
            return audienceOf(option(row));
        return audienceOf(option ?? 'all');
    }
    async retract(table: string, keys: Iterable<Json>): Promise<{ retracted: number }> {
        const decl = this.declaration(table);
        let retracted = 0;
        for (const row of keys) {
            const key = rowKey(decl, row);
            const found = await this.tx.query({ sql: 'SELECT DISTINCT file FROM public_rows WHERE file LIKE ? AND row_key=? AND rev_to IS NULL', parameters: [decl.path + '/%', key] }, { maxRows: 64, maxBytes: 65536 });
            for (const foundRow of found) {
                const file = String(foundRow.file);
                await this.tx.execute({ sql: 'UPDATE public_rows SET rev_to=? WHERE file=? AND row_key=? AND rev_to IS NULL', parameters: [this.state.rev, file, key] });
                await this.tx.execute({ sql: 'DELETE FROM public_rows WHERE file=? AND row_key=? AND rev_from=?', parameters: [file, key, this.state.rev] });
                await this.aggregate(file, decl);
                retracted++;
            }
        }
        return { retracted };
    }
    async replaceShard(table: string, shardValue: string, rows: Iterable<Json>): Promise<{ inserted: number; updated: number; retracted: number }> {
        const decl = this.declaration(table);
        const file = shardFileForValue(decl, shardValue);
        const live = await this.liveRows(file);
        const entries: Entry[] = [];
        let updated = 0, inserted = 0;
        const seen = new Set<string>();
        for (const row of rows) {
            const canonical = canonicalRow(decl, row);
            const entry: Entry = { decl, line: JSON.stringify(canonical), file, key: rowKey(decl, canonical), sort: sortKey(decl, canonical), audience: 'all' };
            if (seen.has(entry.key))
                continue;
            seen.add(entry.key);
            entries.push(entry);
            const current = live.get(entry.key);
            if (!current)
                inserted++;
            else if (!same(current.line, current.sort, current.audience, entry))
                updated++;
        }
        await this.apply(entries);
        const stale = [...live.keys()].filter(key => !seen.has(key));
        if (stale.length) {
            await this.many('UPDATE public_rows SET rev_to=? WHERE file=? AND row_key=? AND rev_to IS NULL', stale.map(key => [this.state.rev, file, key]));
            await this.aggregate(file, decl);
        }
        return { inserted, updated, retracted: stale.length };
    }
    async publish(path: string, body: Uint8Array | { readonly artifact: ArtifactRef }, options?: { readonly media?: string; readonly audience?: Audience; readonly meta?: Json }): Promise<void> {
        const doc = this.docs.get(path);
        const inline = body instanceof Uint8Array;
        await this.versionNode(path, {
            kind: 'doc', parent: parentOf(path), provider: 'public', nodeId: path,
            media: options?.media ?? doc?.media ?? 'text/markdown',
            meta: canonicalJson({ ...(doc ? { title: doc.title } : {}), ...(options?.meta && typeof options.meta === 'object' && !Array.isArray(options.meta) ? options.meta : {}) }),
            audience: audienceOf(options?.audience ?? 'all'),
            ...(inline ? { body, bytes: body.byteLength } : { artifact: body.artifact.id, bytes: Number(body.artifact.bytes) })
        });
    }
    async unpublish(path: string): Promise<void> {
        const [live] = await this.tx.query({ sql: 'SELECT rowid AS id,rev_from FROM public_nodes WHERE path=? AND rev_to IS NULL', parameters: [path] }, { maxRows: 1, maxBytes: 4096 });
        if (!live)
            throw fault('PUBLIC_MISSING', path);
        if (String(live.rev_from) === String(this.state.rev))
            await this.tx.execute({ sql: 'DELETE FROM public_nodes WHERE rowid=?', parameters: [live.id as bigint] });
        else
            await this.tx.execute({ sql: 'UPDATE public_nodes SET rev_to=? WHERE rowid=?', parameters: [this.state.rev, live.id as bigint] });
    }
    async setMeta(path: string, patch: Json): Promise<void> {
        if (patch === null || typeof patch !== 'object' || Array.isArray(patch))
            throw fault('PUBLIC_META', path);
        const [live] = await this.tx.query({ sql: 'SELECT rowid AS id,rev_from,kind,parent,provider,node_id,media,meta,audience,body,artifact,bytes FROM public_nodes WHERE path=? AND rev_to IS NULL', parameters: [path] }, { maxRows: 1, maxBytes: 8388608 });
        const decl = this.tables.get(path), doc = this.docs.get(path);
        if (!live && !decl && !doc)
            throw fault('PUBLIC_MISSING', path);
        const meta = canonicalJson({ ...(live ? JSON.parse(String(live.meta)) as Record<string, Json> : decl ? { title: decl.title, description: decl.description } : { title: doc!.title }), ...patch as Record<string, Json> });
        const spec: NodeSpec = live ? {
            kind: live.kind as PublicNode['kind'], parent: String(live.parent), provider: String(live.provider), nodeId: String(live.node_id),
            media: String(live.media), meta, audience: String(live.audience),
            ...(live.body === null || live.body === undefined ? {} : { body: live.body as Uint8Array }),
            ...(typeof live.artifact === 'string' ? { artifact: live.artifact } : {}),
            ...(live.bytes === null || live.bytes === undefined ? {} : { bytes: Number(live.bytes) })
        } : { kind: decl ? 'dir' : 'doc', parent: parentOf(path), provider: 'public', nodeId: path, media: decl ? 'application/json' : doc!.media, meta, audience: 'all' };
        if (live)
            await this.versionNode(path, spec);
        else
            await this.insertNode(path, spec);
    }
}
export function publicWriter(tx: SqliteTransaction, tables: ReadonlyMap<string, PublicTableDecl>, docs: ReadonlyMap<string, PublicDocDecl>): PublicWriter {
    return new PublicWriterImpl(tx, tables, docs);
}
