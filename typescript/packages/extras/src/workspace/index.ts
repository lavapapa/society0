import type { Initializer } from '@society0/core/initializers';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ root: Schema.string().default('run.workspace'), maxObjectBytes: Schema.natural().min(1).default(67108864) });
export const metadata: PluginMetadata = { "title": "workspace", "summary": "主体工作区与结果文件的版本目录；正文先封存，目录通过受资格检查的原生短事务更新。", "configVersion": 1, "configPolicy": [{ "path": "/root", "mode": "new-run", "affectsResults": true, "secret": false }, { "path": "/maxObjectBytes", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "run", "allowedScopes": ["run"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./workspace", "provides": [{ "key": "workspace", "protocol": "workspace", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "resources", "protocol": "resources", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import { posix } from 'node:path';
import type { SqliteReadView, SqliteTransaction, SqliteMigration } from '@society0/core/sqlite';
import type { ExplorationNode, ExplorationMutation } from '@society0/core/contracts/exploration';
import type { Json } from '@society0/core/contracts/common';
import type { DiscoveryScope, PageRequest, PayloadRange } from '@society0/core/contracts/data';
import type { ActionExecutionContext } from '@society0/core/contracts/domain';
import { after, identity, page, equal, fail, sized } from '@society0/core/information';
import '@society0/core/artifacts-local';
import '@society0/core/sqlite';
export function workspaceMigration(source: Readonly<Record<string, unknown>>): SqliteMigration { return { owner: 'workspace', from: 0, to: 1, after: ['artifacts-local'], source, statements: [{ sql: `CREATE TABLE workspace_nodes(actor_id TEXT NOT NULL,path TEXT NOT NULL,parent TEXT NOT NULL,node_id TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,artifact TEXT,PRIMARY KEY(actor_id,path),UNIQUE(actor_id,node_id));CREATE INDEX workspace_parent ON workspace_nodes(actor_id,parent,path);CREATE TABLE workspace_provenance(actor_id TEXT NOT NULL,node_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(actor_id,node_id),FOREIGN KEY(actor_id,node_id) REFERENCES workspace_nodes(actor_id,node_id) ON DELETE CASCADE);` }] }; }
async function initializeWorkspaceRoots(tx: SqliteTransaction, actorId: string) {
    for (const path of ['/workspace', '/results'])
        await tx.execute({ sql: "INSERT INTO workspace_nodes VALUES(?,?,?,?,'directory',1,NULL) ON CONFLICT(actor_id,path) DO NOTHING", parameters: [actorId, path, '', randomUUID()] });
}

export function workspaceInitializer(options: { readonly id?: string; readonly after: readonly string[] }): Initializer {
    const id = options.id ?? 'workspace.seed';
    return { id, after: options.after, async initialize(context) {
        const operationIds: string[] = [];
        let last: string | null = null, batch = 0;
        while (true) {
            context.signal.throwIfAborted();
            const view = await context.database.snapshot({ maxAgeMs: 30000 });
            let actors;
            try {
                actors = await view.query({ sql: 'SELECT id FROM s0_actors WHERE (? IS NULL OR id>?) ORDER BY id LIMIT 32', parameters: [last, last] }, { maxRows: 32, maxBytes: 65536 });
            } finally { await view.close(); }
            if (!actors.length) break;
            const committed = await context.database.transaction(context.writeLease, {
                operationId: id + ':' + JSON.stringify([context.runId, batch++]), maxDurationMs: 15000,
                maxStatements: 128, maxInputBytes: 1048576, maxChangedRows: 128, maxCapturedBytes: 1048576
            }, async tx => {
                for (const actor of actors) {
                    context.signal.throwIfAborted();
                    await initializeWorkspaceRoots(tx, String(actor.id));
                }
                context.signal.throwIfAborted();
            });
            operationIds.push(committed.receipt.operationId);
            last = String(actors[actors.length - 1]!.id);
        }
        return { operationIds };
    } };
}

const qb = { maxRows: 1, maxBytes: 65536 };
const request = (operationId: string) => ({ operationId, maxDurationMs: 15000, maxStatements: 30, maxInputBytes: 65536, maxChangedRows: 1000, maxCapturedBytes: 1048576 });
declare module '@society0/cordis' {
    interface Context {
        workspace: Workspace;
    }
}
export class Workspace extends Service {
    static Config = Config;
    static inject = ['artifacts', 'resources', 'database'];
    readonly id = 'workspace/v1';
    readonly prefix = '/workspace';
    constructor(ctx: Context, private options: {
        root: string;
        maxObjectBytes: number;
    }) { super(ctx, 'workspace'); }
    private path(path: string) {
        if (!path.isWellFormed() || !path.startsWith('/') || posix.normalize(path) !== path || !['/workspace', '/results'].some(root => path === root || path.startsWith(root + '/')))
            return fail('WORKSPACE_PATH');
        return path;
    }
    private node(row: any): ExplorationNode { return { path: String(row.path), objectId: String(row.node_id), kind: row.kind, versions: [{ owner: 'workspace:' + this.options.root + ':' + row.actor_id, version: String(row.version) }], ...(row.artifact ? { rawBytes: JSON.parse(row.artifact).bytes } : {}), ...(row.provenance ? { provenance: JSON.parse(row.provenance) } : {}) }; }
    initialize(tx: SqliteTransaction, actorId: string) { return initializeWorkspaceRoots(tx, actorId); }
    private async row(path: string, scope: DiscoveryScope, reader: SqliteReadView | SqliteTransaction) { this.path(path); const [row] = await reader.query({ sql: 'SELECT n.*,p.body AS provenance FROM workspace_nodes n LEFT JOIN workspace_provenance p USING(actor_id,node_id) WHERE n.actor_id=? AND n.path=?', parameters: [scope.actorId, path] }, qb); return row; }
    async visible(path: string, scope: DiscoveryScope, reader: SqliteReadView) { return !!await this.row(path, scope, reader); }
    async stat(path: string, scope: DiscoveryScope, reader: SqliteReadView) {
        const row = await this.row(path, scope, reader);
        if (!row)
            return fail('WORKSPACE_MISSING');
        return this.node(row);
    }
    async list(path: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        const node = await this.stat(path, scope, view);
        if (node.kind !== 'directory')
            return fail('WORKSPACE_NOT_DIRECTORY');
        const id = identity(scope, view, node), last = after(request.cursor, id) ?? '';
        const [count] = await view.query({ sql: 'SELECT count(*) AS n FROM workspace_nodes WHERE actor_id=? AND parent=?', parameters: [scope.actorId, path] }, qb);
        const rows = await view.query({ sql: 'SELECT n.*,p.body AS provenance FROM workspace_nodes n LEFT JOIN workspace_provenance p USING(actor_id,node_id) WHERE n.actor_id=? AND n.parent=? AND n.path>? ORDER BY n.path LIMIT ?', parameters: [scope.actorId, path, last, request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: Math.max(65536, request.maxResponseBytes * 2) });
        return page(rows.map(row => this.node(row)), String(count!.n), view.revision, request, id, x => x.path);
    }
    async open(node: ExplorationNode, scope: DiscoveryScope, view: SqliteReadView) {
        const row = await this.row(node.path, scope, view);
        if (!row || !equal(this.node(row), node))
            return fail('WORKSPACE_STALE');
        if (!row.artifact)
            return fail('WORKSPACE_NOT_FILE');
        const object = JSON.parse(String(row.artifact)), pin = await this.ctx.artifacts.localFile(object), artifacts = this.ctx.artifacts;
        let released = false;
        return { value: { node, read: async function* (range: PayloadRange, signal: AbortSignal) {
                    if (released)
                        return fail('WORKSPACE_RELEASED');
                    if (range.length > range.maxBytes)
                        return fail('PAYLOAD_RANGE');
                    yield* artifacts.read(object, { offset: range.offset, length: range.length }, { signal });
                } }, release: async () => { released = true; await pin.release(); } };
    }
    async collect(path: string, query: unknown, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) { const result = await this.list(path, scope, view, request); return sized({ ...result, items: result.items.map(node => ({ kind: 'inline' as const, value: JSON.parse(JSON.stringify(node)) })), responseBytes: 0 }, request.maxResponseBytes); }
    private async expected(path: string, expected: ExplorationNode | null, scope: DiscoveryScope, tx: SqliteTransaction) {
        const row = await this.row(path, scope, tx);
        if (!equal(row ? this.node(row) : null, expected))
            return fail('WORKSPACE_STALE');
        return row;
    }
    private async parent(path: string, scope: DiscoveryScope, view: SqliteReadView) {
        const node = await this.stat(posix.dirname(path), scope, view);
        if (node.kind !== 'directory')
            return fail('WORKSPACE_NOT_DIRECTORY');
        return node;
    }
    private async touch(tx: SqliteTransaction, scope: DiscoveryScope, path: string) { await tx.execute({ sql: 'UPDATE workspace_nodes SET version=version+1 WHERE actor_id=? AND path=?', parameters: [scope.actorId, path] }); }
    async writeFile(path: string, source: AsyncIterable<Uint8Array>, expected: ExplorationNode | null, scope: DiscoveryScope, context: ActionExecutionContext, provenance?: Json) {
        this.path(path);
        if (context.actorId !== scope.actorId)
            return fail('WORKSPACE_NOT_VISIBLE');
        const view = await context.database.snapshot({ maxAgeMs: 30000 });
        let parent: ExplorationNode;
        try {
            parent = await this.parent(path, scope, view);
        }
        finally {
            await view.close();
        }
        const max = this.options.maxObjectBytes;
        let total = 0;
        const bounded = async function* () {
            for await (const chunk of source) {
                context.signal.throwIfAborted();
                total += chunk.length;
                if (total > max)
                    return fail('WORKSPACE_OBJECT_BUDGET');
                for (let offset = 0; offset < chunk.length; offset += 65536)
                    yield chunk.subarray(offset, offset + 65536);
            }
        };
        const object = await this.ctx.artifacts.stage(bounded(), 'application/octet-stream', { signal: context.signal });
        const result = await context.transaction(request(context.operationId + ':file:' + randomUUID()), async (tx) => {
            await this.expected(parent.path, parent, scope, tx);
            const current = await this.expected(path, expected, scope, tx);
            if (current && current.kind !== 'file')
                return fail('WORKSPACE_NOT_FILE');
            await this.ctx.artifacts.reference(tx, object);
            await tx.execute({ sql: "INSERT INTO workspace_nodes VALUES(?,?,?,?,'file',1,?) ON CONFLICT(actor_id,path) DO UPDATE SET artifact=excluded.artifact,version=workspace_nodes.version+1", parameters: [scope.actorId, path, parent.path, randomUUID(), JSON.stringify(object)] });
            const inserted = await this.row(path, scope, tx);
            await tx.execute({ sql: 'INSERT INTO workspace_provenance VALUES(?,?,?) ON CONFLICT(actor_id,node_id) DO UPDATE SET body=excluded.body', parameters: [scope.actorId, String(inserted!.node_id), JSON.stringify({ operationId: context.operationId, runId: context.runId, actorId: scope.actorId, momentId: scope.momentId, query: scope.query, source: provenance ?? null })] });
            await this.touch(tx, scope, parent.path);
            return this.node((await this.row(path, scope, tx))!);
        });
        return result.value;
    }
    private async mutationParent(path: string, scope: DiscoveryScope, context: ActionExecutionContext) { const view = await context.database.snapshot({ maxAgeMs: 30000 }); try {
        return await this.parent(path, scope, view);
    }
    finally {
        await view.close();
    } }
    private async createDirectory(mutation: Extract<ExplorationMutation, {
        kind: 'mkdir';
    }>, from: string, scope: DiscoveryScope, tx: SqliteTransaction) {
        if (posix.dirname(from) !== mutation.expectedParent.path || mutation.expectedParent.kind !== 'directory')
            return fail('WORKSPACE_PARENT');
        await this.expected(mutation.expectedParent.path, mutation.expectedParent, scope, tx);
        await this.expected(from, null, scope, tx);
        await tx.execute({ sql: "INSERT INTO workspace_nodes VALUES(?,?,?,?,'directory',1,NULL)", parameters: [scope.actorId, from, mutation.expectedParent.path, randomUUID()] });
        await this.touch(tx, scope, mutation.expectedParent.path);
    }
    private async renameNode(mutation: Extract<ExplorationMutation, {
        kind: 'rename';
    }>, from: string, sourceParent: ExplorationNode, scope: DiscoveryScope, tx: SqliteTransaction) {
        this.path(mutation.to);
        if (mutation.to === from || mutation.to.startsWith(from + '/'))
            return fail('WORKSPACE_RENAME_TARGET');
        if (posix.dirname(mutation.to) !== mutation.expectedParent.path || mutation.expectedParent.kind !== 'directory')
            return fail('WORKSPACE_PARENT');
        await this.expected(mutation.expectedParent.path, mutation.expectedParent, scope, tx);
        await this.expected(mutation.to, mutation.targetExpected, scope, tx);
        if (mutation.targetExpected) {
            const [children] = await tx.query({ sql: 'SELECT count(*) AS n FROM workspace_nodes WHERE actor_id=? AND parent=?', parameters: [scope.actorId, mutation.to] }, qb);
            if (BigInt(String(children!.n)) > 0n)
                return fail('WORKSPACE_NOT_EMPTY');
            await tx.execute({ sql: 'DELETE FROM workspace_nodes WHERE actor_id=? AND path=?', parameters: [scope.actorId, mutation.to] });
        }
        await tx.execute({ sql: "UPDATE workspace_nodes SET path=? || substr(path,length(?)+1),parent=CASE WHEN path=? THEN ? ELSE ? || substr(parent,length(?)+1) END,version=version+1 WHERE actor_id=? AND (path=? OR substr(path,1,length(?)+1)=? || '/')", parameters: [mutation.to, from, from, mutation.expectedParent.path, mutation.to, from, scope.actorId, from, from, from] });
        if (mutation.expectedParent.path !== sourceParent!.path)
            await this.touch(tx, scope, mutation.expectedParent.path);
    }
    private async removeNode(from: string, scope: DiscoveryScope, tx: SqliteTransaction) { const [count] = await tx.query({ sql: 'SELECT count(*) AS n FROM workspace_nodes WHERE actor_id=? AND parent=?', parameters: [scope.actorId, from] }, qb); if (BigInt(String(count!.n)) > 0n)
        return fail('WORKSPACE_NOT_EMPTY'); await tx.execute({ sql: 'DELETE FROM workspace_nodes WHERE actor_id=? AND path=?', parameters: [scope.actorId, from] }); }
    async mutate(mutation: ExplorationMutation, scope: DiscoveryScope, context: ActionExecutionContext) {
        if (context.actorId !== scope.actorId)
            return fail('WORKSPACE_NOT_VISIBLE');
        const from = mutation.kind === 'rename' ? mutation.from : mutation.path;
        this.path(from);
        if (mutation.kind === 'rename')
            this.path(mutation.to);
        if (from === '/workspace' || from === '/results')
            return fail('WORKSPACE_ROOT');
        if (mutation.kind === 'mkdir') {
            await context.transaction(request(context.operationId + ':mutate:' + randomUUID()), tx => this.createDirectory(mutation, from, scope, tx));
            return;
        }
        const parent = await this.mutationParent(from, scope, context);
        await context.transaction(request(context.operationId + ':mutate:' + randomUUID()), async (tx) => {
            await this.expected(parent.path, parent, scope, tx);
            await this.expected(from, mutation.expected, scope, tx);
            if (mutation.kind === 'remove')
                await this.removeNode(from, scope, tx);
            else
                await this.renameNode(mutation, from, parent, scope, tx);
            await this.touch(tx, scope, parent.path);
        });
    }
}
export default Workspace;
