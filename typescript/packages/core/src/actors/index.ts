import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { Json } from '../contracts/common.js';
import type { SqliteReader } from '../contracts/eligibility.js';
import type { SqliteTransaction } from '../sqlite/contracts.js';
import '../sqlite/index.js';
import '../initializers/index.js';
import { ensureActorsTable, writePublicActor } from '../plain/public.js';
export * from './schema.js';
export interface Actor {
    readonly id: string;
    readonly attributes: Json;
    readonly source?: Json;
}
export const Config = Schema.object({ seed: Schema.boolean().default(false) });
export const metadata: PluginMetadata = { title: 'actors', summary: 'SQL主体身份、属性与参与状态', configVersion: 1, configPolicy: [{ path: '/seed', mode: 'new-run', affectsResults: true }], scope: 'run', hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './actors', provides: [{ key: 'actors', protocol: 'actors', major: 1, capabilities: [] }], requires: [{ service: { key: 'database', protocol: 'database', major: 1, capabilities: [] }, optional: false, phase: 'module' }] };
declare module '@society0/host' {
    interface Context {
        actors: Actors;
    }
}
export class Actors extends Service {
    static Config = Config;
    static inject = ['database'];
    private readonly seed: boolean;
    constructor(ctx: Context, input = {}) { super(ctx, 'actors'); this.seed = Config(input).seed; }
    [Service.init]() {
        if (!this.seed)
            return;
        ensureActorsTable(this.ctx);
        this.ctx.inject(['initializers'], scoped => scoped.initializers.register({
            id: 'actors.seed', after: [],
            initialize: context => this.seedWorld(context)
        }));
    }
    private async seedWorld(context: Parameters<Parameters<Context['initializers']['register']>[0]['initialize']>[0]) {
        const world = context.input as { actors?: readonly { id?: unknown; attributes?: unknown; source?: unknown }[] } | undefined;
        const actors = world?.actors;
        if (actors === undefined)
            return { operationIds: [] };
        if (!Array.isArray(actors))
            throw Error('ACTOR_WORLD_SHAPE');
        const unique = new Map<string, { id: string; attributes: Json; source: Json }>();
        for (const [index, actor] of actors.entries()) {
            if (!actor || typeof actor !== 'object' || typeof actor.id !== 'string' || !actor.id)
                throw Error('ACTOR_WORLD_SHAPE actors[' + index + '].id');
            if (!('source' in actor) || actor.source === null || actor.source === '')
                throw Error('ACTOR_SOURCE_MISSING ' + actor.id);
            if (!unique.has(actor.id))
                unique.set(actor.id, { id: actor.id, attributes: (actor.attributes ?? {}) as Json, source: actor.source as Json });
        }
        const operations: string[] = [];
        const list = [...unique.values()];
        for (let offset = 0; offset < list.length; offset += 64) {
            const operationId = 'actors.seed/' + offset;
            await context.database.transaction(context.writeLease, { operationId, maxDurationMs: 15000, maxStatements: 256, maxInputBytes: 1048576, maxChangedRows: 128, maxCapturedBytes: 1048576 }, async tx => {
                for (const actor of list.slice(offset, offset + 64)) {
                    if (await this.get(actor.id, tx))
                        continue;
                    await this.create(tx, actor);
                    const state = await this.state(actor.id, tx);
                    if (state)
                        await writePublicActor(this.ctx, tx, { id: actor.id, participation: state.participation, version: state.version, attributes: actor.attributes });
                }
            });
            operations.push(operationId);
        }
        return { operationIds: operations };
    }
    async get(id: string, reader: SqliteReader): Promise<Actor | undefined> { const [row] = await reader.query({ sql: 'SELECT id,attributes FROM s0_actors WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: 1048576 }); return row ? { id: String(row.id), attributes: JSON.parse(String(row.attributes)) } : undefined; }
    async create(tx: SqliteTransaction, actor: Actor): Promise<void> { await tx.execute({ sql: "INSERT INTO s0_actors(id,attributes,source,participation,reason,version) VALUES (?,?,?,'active','null',1)", parameters: [actor.id, JSON.stringify(actor.attributes), actor.source === undefined ? null : JSON.stringify(actor.source)] }); }
    async update(tx: SqliteTransaction, actor: Actor): Promise<void> {
        const result = await tx.execute({ sql: 'UPDATE s0_actors SET attributes=?,version=version+1 WHERE id=?', parameters: [JSON.stringify(actor.attributes), actor.id] });
        if (result.changes !== 1n)
            throw Error('ACTOR_MISSING ' + actor.id);
    }
    async state(id: string, reader: SqliteReader) { const [row] = await reader.query({ sql: 'SELECT participation,reason,version FROM s0_actors WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: 1048576 }); return row ? { participation: row.participation as 'active' | 'inactive', reason: JSON.parse(String(row.reason)) as Json, version: String(row.version) } : undefined; }
    async participation(id: string, reader: SqliteReader): Promise<'active' | 'inactive'> {
        const state = await this.state(id, reader);
        if (!state)
            throw Error('ACTOR_MISSING ' + id);
        return state.participation;
    }
    async setParticipation(tx: SqliteTransaction, id: string, state: 'active' | 'inactive', reason: Json): Promise<void> {
        const result = await tx.execute({ sql: 'UPDATE s0_actors SET participation=?,reason=?,version=version+1 WHERE id=?', parameters: [state, JSON.stringify(reason), id] });
        if (result.changes !== 1n)
            throw Error('ACTOR_MISSING ' + id);
    }
}
export default Actors;
