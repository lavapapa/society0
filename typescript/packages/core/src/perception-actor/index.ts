import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "perception-actor", "summary": "提供主体画像、状态及提醒的具名默认感知来源。", "configVersion": 1, "configPolicy": [], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./perception-actor", "provides": [], "requires": [{ "service": { "key": "perceptions", "protocol": "perceptions", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actors", "protocol": "actors", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/cordis';
import type { PerceptionSource } from '../contracts/perception.js';
import { bytes, fail } from '../information/page.js';
import '../perception/index.js';
import '../actors/index.js';
export default { Config, inject: ['perceptions', 'actors'], apply(ctx: Context) {
        const source: PerceptionSource = { id: 'actor/v1', schema: { type: 'object' }, cursorScope: 'thread', available: async (scope, reader) => !!await ctx.actors.get(scope.actorId, reader), versions: async (scope, reader) => { const [row] = await reader.query({ sql: 'SELECT version FROM s0_actors WHERE id=?', parameters: [scope.actorId] }, { maxRows: 1, maxBytes: 1024 }); return [{ owner: 'actors:' + scope.actorId, version: String(row?.version ?? 0) }]; }, collect: async (scope, after, through, reader, bounds) => {
                const versions = await source.versions(scope, reader), version = versions[0]!.version;
                const actor = await ctx.actors.get(scope.actorId, reader);
                const page = { items: after === version ? [] : [{ id: scope.actorId + ':' + version, kind: 'actor', material: { kind: 'inline' as const, value: { actor: actor as any, participation: await ctx.actors.participation(scope.actorId, reader) } }, provenance: { source: 'actor/v1', versions: versions.map(v => ({ owner: v.owner, version: v.version })) } }], nextCursor: version, through: version, hasMore: false };
                if (bytes(page) > bounds.maxResponseBytes)
                    return fail('PERCEPTION_BUDGET');
                return page;
            } };
        ctx.perceptions.register(source);
    } };
