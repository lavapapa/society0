import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "perception", "summary": "登记多来源感知；独立actor/thread游标，原文与Driver输入链接同事务提交。", "configVersion": 1, "configPolicy": [], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./perception", "provides": [{ "key": "perceptions", "protocol": "perceptions", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "threads", "protocol": "threads", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import { slotKey } from '@society0/host/slots';
import type { Invocation, Lease, Json } from '../contracts/common.js';
import type { PageBounds } from '../contracts/data.js';
import type { PerceptionSource, PerceptionScope, PerceptionBatch, PerceptionInput, PerceptionSelection, PerceptionRead } from '../contracts/perception.js';
import type { SqliteReadView, SqliteTransaction, SqliteMigration } from '../sqlite/contracts.js';
import { equal, fail, bytes } from '../information/page.js';
import { attachValue } from '../results/payload.js';
import '../thread/index.js';
import '../sqlite/index.js';
import { perceptionSourceSlot, type SourceRegistration } from './registry.js';
import { PerceptionSession, selectSources } from './selection.js';
export function perceptionMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return { owner: 'perception', from: 0, to: 1, after: ['thread'], source, statements: [{ sql: `CREATE TABLE perception_progress(actor_id TEXT NOT NULL,source_id TEXT NOT NULL,domain_id TEXT NOT NULL,cursor TEXT NOT NULL,through TEXT,PRIMARY KEY(actor_id,source_id,domain_id));
CREATE TABLE perception_material(batch_id TEXT NOT NULL,source_id TEXT NOT NULL,percept_id TEXT NOT NULL,thread_id TEXT NOT NULL,kind TEXT NOT NULL,material TEXT NOT NULL,provenance TEXT NOT NULL,PRIMARY KEY(batch_id,source_id,percept_id));
CREATE TABLE perception_links(batch_id TEXT NOT NULL,source_id TEXT NOT NULL,percept_id TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(batch_id,source_id,percept_id),FOREIGN KEY(batch_id,source_id,percept_id) REFERENCES perception_material(batch_id,source_id,percept_id));` }] };
}
declare module '@society0/cordis' {
    interface Context {
        perceptions: Perceptions;
    }
}
export class Perceptions extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts', 'slots', 'threads'];
    constructor(ctx: Context) { super(ctx, 'perceptions'); ctx.slots.declare({ key: perceptionSourceSlot, strategy: 'keyed', required: false, validate: validateSource }); }
    register(source: PerceptionSource) { return this.ctx.slots.contribute(perceptionSourceSlot, { id: source.id, value: { source, registrationId: randomUUID() } }); }
    async acquire(ids: readonly string[], context: Invocation): Promise<Lease<PerceptionSelection>> {
        const selected = await this.ctx.slots.acquire(perceptionSourceSlot, context);
        try {
            const session = new PerceptionSession(this.ctx, selected, selectSources(ids, selected), context);
            return { value: session, release: () => session.release() };
        }
        catch (error) {
            await selected.release();
            throw error;
        }
    }
}
function validateSource(value: unknown): SourceRegistration {
    const entry = value as SourceRegistration;
    if (typeof entry?.source?.collect !== 'function' || !['actor', 'thread'].includes(entry?.source?.cursorScope))
        return fail('PERCEPTION_INVALID');
    return entry;
}
export default Perceptions;
