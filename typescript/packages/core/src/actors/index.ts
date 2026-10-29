import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { Json } from '../contracts/common.js';
import type { SqliteReader } from '../contracts/eligibility.js';
import type { SqliteTransaction } from '../sqlite/contracts.js';
import '../sqlite/index.js';
export * from './schema.js';
export interface Actor {
    readonly id: string;
    readonly attributes: Json;
}
export const Config = Schema.object({});
export const metadata: PluginMetadata = { title: 'actors', summary: 'SQL主体身份、属性与参与状态', configVersion: 1, configPolicy: [], scope: 'run', hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './actors', provides: [{ key: 'actors', protocol: 'actors', major: 1, capabilities: [] }], requires: [{ service: { key: 'database', protocol: 'database', major: 1, capabilities: [] }, optional: false, phase: 'module' }] };
declare module '@society0/host' {
    interface Context {
        actors: Actors;
    }
}
export class Actors extends Service {
    static Config = Config;
    static inject = ['database'];
    constructor(ctx: Context) { super(ctx, 'actors'); }
    async get(id: string, reader: SqliteReader): Promise<Actor | undefined> { const [row] = await reader.query({ sql: 'SELECT id,attributes FROM s0_actors WHERE id=?', parameters: [id] }, { maxRows: 1, maxBytes: 1048576 }); return row ? { id: String(row.id), attributes: JSON.parse(String(row.attributes)) } : undefined; }
    async create(tx: SqliteTransaction, actor: Actor): Promise<void> { await tx.execute({ sql: "INSERT INTO s0_actors VALUES (?,?,'active','null',1)", parameters: [actor.id, JSON.stringify(actor.attributes)] }); }
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
