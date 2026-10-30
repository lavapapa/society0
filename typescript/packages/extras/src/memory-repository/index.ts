import { sameProjection } from '../vector-providers/types.js';
import { Context, Service } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { ApiEntry, PluginMetadata } from '@society0/host/module-resolver';
import type { Json } from '@society0/core/contracts/common';
import type { PageRequest } from '@society0/core/contracts/data';
import type { ArtifactRef } from '@society0/core/artifacts-local';
import type { SqliteMigration, SqliteReadView, SqliteTransaction, SqliteRow } from '@society0/core/sqlite';
import { after, page, bytes, fail } from '@society0/core/information/page';
import '@society0/core/thread';
import '@society0/core/artifacts-local';
import '@society0/core/sqlite';
import type { MemoryVector, MemoryChange, MemoryChangeCursor, MemoryIndexProgress } from '../vector-providers/types.js';
export type { MemoryVector, MemoryChange, MemoryChangeCursor, MemoryIndexProgress };
import type { Experience } from '@society0/core/contracts/cognition';
export type { Experience } from '@society0/core/contracts/cognition';
export const Config = Schema.object({ maxExperienceBytes: Schema.natural().min(1).default(16384), maxVectorBytes: Schema.natural().min(1).default(131072), maxPageItems: Schema.natural().min(1).default(256) });
export const metadata: PluginMetadata = { title: 'memory-repository', summary: 'Authoritative experience facts and original vectors in run SQLite', configVersion: 1, configPolicy: [], scope: 'run', allowedScopes: ['run'], hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './memory-repository', provides: [{ key: 'memoryRepository', protocol: 'memoryRepository', major: 1, capabilities: [] }], requires: ['database', 'threads', 'artifacts'].map(key => ({ service: { key, protocol: key, major: 1, capabilities: [] }, optional: false, phase: 'module' })) };
export function memoryMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return { owner: 'memory-repository', from: 0, to: 1, after: ['thread', 'artifacts-local'], source, statements: [{ sql: `CREATE TABLE memory_experiences(id TEXT PRIMARY KEY NOT NULL,actor_id TEXT NOT NULL,thread_id TEXT NOT NULL REFERENCES threads(id),body TEXT NOT NULL,artifacts TEXT NOT NULL,vector TEXT,revision INTEGER NOT NULL);
CREATE INDEX memory_actor ON memory_experiences(actor_id,id);
CREATE INDEX memory_vectors ON memory_experiences(revision,id);
CREATE TABLE memory_changes(revision INTEGER NOT NULL,id TEXT NOT NULL,actor_id TEXT NOT NULL,kind TEXT NOT NULL,vector TEXT,PRIMARY KEY(revision,id));
CREATE TABLE memory_index_progress(generation TEXT PRIMARY KEY NOT NULL,progress TEXT NOT NULL);` }] };
}
function experience(row: SqliteRow): Experience { return { id: String(row.id), actorId: String(row.actor_id), threadId: String(row.thread_id), body: JSON.parse(String(row.body)), artifacts: JSON.parse(String(row.artifacts)) }; }
function change(row: SqliteRow): MemoryChange { const common = { cursor: { revision: String(row.revision), id: String(row.id) }, actorId: String(row.actor_id) }; return row.kind === 'delete' ? { ...common, kind: 'delete' } : { ...common, kind: 'upsert', vector: JSON.parse(String(row.vector)) }; }
function memoryText(value: string, field: string): void { if (!value.isWellFormed()) throw Object.assign(new Error(`MEMORY_TEXT_NOT_WELL_FORMED: ${field} contains a lone surrogate`), { code: 'MEMORY_TEXT_NOT_WELL_FORMED', field }); }
declare module '@society0/cordis' {
    interface Context {
        memoryRepository: MemoryRepository;
    }
}
export class MemoryRepository extends Service {
    static Config = Config;
    static inject = ['database', 'threads', 'artifacts'];
    constructor(ctx: Context, private options: ReturnType<typeof Config>) { super(ctx, 'memoryRepository'); }
    private run(view: SqliteReadView | SqliteTransaction) { if (view.runId !== this.ctx.database.runId)
        fail('MEMORY_RUN_MISMATCH'); }
    private limit(request: PageRequest) { if (!Number.isSafeInteger(request.maxItems) || request.maxItems < 1 || request.maxItems > this.options.maxPageItems)
        fail('MEMORY_PAGE_BUDGET'); }
    async read(view: SqliteReadView, id: string): Promise<Experience | undefined> { this.run(view); memoryText(id, 'id'); const [row] = await view.query({ sql: 'SELECT * FROM memory_experiences WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: this.options.maxExperienceBytes + this.options.maxVectorBytes + 1024 }); return row ? experience(row) : undefined; }
    private validateVector(vector: MemoryVector | undefined): void { if (!vector)
        return; if (bytes(vector) > this.options.maxVectorBytes || vector.vector.length !== vector.identity.dimensions || vector.vector.some(n => !Number.isFinite(n)))
        fail('MEMORY_VECTOR_BUDGET'); }
    async append(tx: SqliteTransaction, value: Experience, vector?: MemoryVector): Promise<void> {
        this.run(tx);
        memoryText(value.id, 'id');
        memoryText(value.actorId, 'actorId');
        memoryText(value.threadId, 'threadId');
        if (bytes(value) > this.options.maxExperienceBytes)
            fail('MEMORY_EXPERIENCE_BUDGET');
        this.validateVector(vector);
        const [thread] = await tx.query({ sql: 'SELECT actor_id FROM threads WHERE id=?', parameters: [value.threadId] }, { maxRows: 1, maxBytes: 4096 });
        if (!thread || thread.actor_id !== value.actorId)
            fail('MEMORY_THREAD_IDENTITY');
        for (const ref of value.artifacts)
            await this.ctx.artifacts.reference(tx, ref);
        const revision = BigInt(tx.baseRevision) + 1n, encoded = vector ? JSON.stringify(vector) : null;
        await tx.execute({ sql: 'INSERT INTO memory_experiences VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET actor_id=excluded.actor_id,thread_id=excluded.thread_id,body=excluded.body,artifacts=excluded.artifacts,vector=excluded.vector,revision=excluded.revision', parameters: [value.id, value.actorId, value.threadId, JSON.stringify(value.body), JSON.stringify(value.artifacts), encoded, revision] });
        await tx.execute({ sql: 'INSERT INTO memory_changes VALUES(?,?,?,?,?) ON CONFLICT(revision,id) DO UPDATE SET actor_id=excluded.actor_id,kind=excluded.kind,vector=excluded.vector', parameters: [revision, value.id, value.actorId, vector ? 'upsert' : 'delete', encoded] });
    }
    async remove(tx: SqliteTransaction, id: string): Promise<void> { this.run(tx); memoryText(id, 'id'); const [row] = await tx.query({ sql: 'SELECT actor_id FROM memory_experiences WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: 4096 }); if (!row)
        return; await tx.execute({ sql: 'DELETE FROM memory_experiences WHERE id=?', parameters: [id] }); await tx.execute({ sql: "INSERT INTO memory_changes VALUES(?,?,?,'delete',NULL) ON CONFLICT(revision,id) DO UPDATE SET kind='delete',vector=NULL", parameters: [BigInt(tx.baseRevision) + 1n, id, String(row.actor_id)] }); }
    async scan(view: SqliteReadView, request: PageRequest & {
        actorId?: string;
        text?: string;
    }) {
        this.run(view);
        this.limit(request);
        const identity = { runId: view.runId, revision: view.revision, actorId: request.actorId ?? null, text: request.text ?? null };
        const last = after(request.cursor, identity) ?? '';
        const params = [request.actorId ?? null, request.actorId ?? null, request.text ?? null, request.text ?? null];
        const filter = '(? IS NULL OR actor_id=?) AND (? IS NULL OR instr(body,?)>0)';
        const [count] = await view.query({ sql: 'SELECT count(*) AS total FROM memory_experiences WHERE ' + filter, parameters: params }, { maxRows: 1, maxBytes: 1024 });
        const rows = await view.query({ sql: 'SELECT id,actor_id,thread_id,body,artifacts FROM memory_experiences WHERE ' + filter + ' AND id>? ORDER BY id LIMIT ?', parameters: [...params, last, request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: request.maxResponseBytes });
        return page(rows.map(experience), String(count!.total), view.revision, request, identity, value => value.id);
    }
    async changes(view: SqliteReadView, request: PageRequest & {
        after?: MemoryChangeCursor;
        throughRevision: string;
    }) {
        this.run(view);
        this.limit(request);
        if (BigInt(request.throughRevision) > BigInt(view.revision))
            fail('MEMORY_REVISION_AHEAD');
        const identity = { runId: view.runId, revision: view.revision, throughRevision: request.throughRevision, kind: 'memory-changes' };
        const cursor: MemoryChangeCursor = after(request.cursor, identity) ?? request.after ?? { revision: '0', id: '' };
        const parameters = [BigInt(cursor.revision), BigInt(cursor.revision), cursor.id, BigInt(request.throughRevision)];
        const where = '(revision>? OR (revision=? AND id>?)) AND revision<=?';
        const [count] = await view.query({ sql: 'SELECT count(*) AS total FROM memory_changes WHERE ' + where, parameters }, { maxRows: 1, maxBytes: 1024 });
        const rows = await view.query({ sql: 'SELECT * FROM memory_changes WHERE ' + where + ' ORDER BY revision,id LIMIT ?', parameters: [...parameters, request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: request.maxResponseBytes });
        return page(rows.map(change), String(count!.total), view.revision, request, identity, value => value.cursor);
    }
    async vectors(view: SqliteReadView, request: PageRequest & {
        after?: MemoryChangeCursor;
    }) {
        this.run(view);
        this.limit(request);
        const cursor = request.after ?? { revision: '0', id: '' };
        const rows = await view.query({ sql: "SELECT revision,id,actor_id,'upsert' AS kind,vector FROM memory_experiences WHERE vector IS NOT NULL AND (revision>? OR (revision=? AND id>?)) ORDER BY revision,id LIMIT ?", parameters: [BigInt(cursor.revision), BigInt(cursor.revision), cursor.id, request.maxItems] }, { maxRows: request.maxItems, maxBytes: request.maxResponseBytes });
        return rows.map(change);
    }
    async lastChange(view: SqliteReadView): Promise<MemoryChange | undefined> { this.run(view); const [row] = await view.query({ sql: 'SELECT * FROM memory_changes ORDER BY revision DESC,id DESC LIMIT 1' }, { maxRows: 1, maxBytes: this.options.maxVectorBytes + 1024 }); return row ? change(row) : undefined; }
    async progress(view: SqliteReadView, generation: string): Promise<MemoryIndexProgress | undefined> { this.run(view); memoryText(generation, 'generation'); const [row] = await view.query({ sql: 'SELECT progress FROM memory_index_progress WHERE generation=?', parameters: [generation] }, { maxRows: 1, maxBytes: 16384 }); return row ? JSON.parse(String(row.progress)) : undefined; }
    async recordProgress(tx: SqliteTransaction, progress: MemoryIndexProgress): Promise<void> { this.run(tx); memoryText(progress.generation, 'generation'); if (progress.runId !== tx.runId || BigInt(progress.throughRevision) > BigInt(tx.baseRevision))
        fail('MEMORY_PROGRESS_IDENTITY'); const [previous] = await tx.query({ sql: 'SELECT progress FROM memory_index_progress WHERE generation=?', parameters: [progress.generation] }, { maxRows: 1, maxBytes: 16384 }); if (previous && !sameProjection(JSON.parse(String(previous.progress)), progress))
        fail('MEMORY_PROGRESS_IDENTITY'); await tx.execute({ sql: 'INSERT INTO memory_index_progress VALUES(?,?) ON CONFLICT(generation) DO UPDATE SET progress=excluded.progress', parameters: [progress.generation, JSON.stringify(progress)] }); }
}
export default MemoryRepository;
