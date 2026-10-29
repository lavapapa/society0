import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "plain", "summary": "提供基础主体信息和行动机制，初态通过独立初始化入口配置。", "configVersion": 1, "configPolicy": [], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./plain", "provides": [], "requires": [{ "service": { "key": "eventHandlers", "protocol": "eventHandlers", "major": 1, "capabilities": [] }, "optional": true, "phase": "module" },{ "service": { "key": "random", "protocol": "random", "major": 1, "capabilities": [] }, "optional": true, "phase": "module" }, { "service": { "key": "actors", "protocol": "actors", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actions", "protocol": "actions", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "information", "protocol": "information", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/host';
import { registerPlainDispatch } from './dispatch.js';
import { sampleRanks, type RandomPort } from './sample.js';
import type { Json } from '../contracts/common.js';
import { after, identity, page, fail } from '../information/page.js';
import '../actors/index.js';
import '../actions/index.js';
import '../information/index.js';
export default { Config, inject: ['actors', 'actions', 'information', 'slots'], apply(ctx: Context) {
        registerPlainDispatch(ctx);
        ctx.actions.register({ name: 'plain.update', inputSchema: { type: 'object', required: ['attributes'], properties: { attributes: {} }, additionalProperties: false }, outputSchema: { type: 'object', required: ['id', 'attributes'] }, effect: 'world', available: async (scope, reader) => !!await ctx.actors.get(scope.actorId, reader), execute: async (input, context) => {
                const actor = { id: context.actorId, attributes: (input as {
                        attributes: Json;
                    }).attributes };
                const operationId = context.operationId;
                await context.transaction({ operationId, maxDurationMs: 15000, maxStatements: 20, maxInputBytes: 1048576, maxChangedRows: 10, maxCapturedBytes: 1048576 }, async (tx) => { await ctx.actors.update(tx, actor); return actor; });
                return { operationId, status: 'completed', value: actor, original: { action: 'plain.update', input }, artifacts: [] };
            } });
        ctx.information.register({ id: 'plain.actors', schema: { type: 'object', properties: { prefix: { type: 'string' }, participation: { enum: ['active', 'inactive'] }, order: { enum: ['asc', 'desc'] }, sample: { type: 'object', required: ['size', 'seed'], properties: { size: { type: 'integer', minimum: 0, maximum: 1000 }, seed: { type: 'string' } }, additionalProperties: false }, fields: { type: 'array', items: { enum: ['id', 'attributes', 'participation', 'reason', 'version'] } } } }, capabilities: ['sql-filter', 'keyset', 'stable-sort', 'projection', 'sql-seeded-sample'], available: async (scope, reader) => !!await ctx.actors.get(scope.actorId, reader), query: async (input, scope, view, request) => {
                const query = (input ?? {}) as {
                    prefix?: string;
                    participation?: string;
                    order?: string;
                    fields?: string[];
                    sample?: {
                        size: number;
                        seed: string;
                    };
                };
                if (query.order && !['asc', 'desc'].includes(query.order) || query.participation && !['active', 'inactive'].includes(query.participation))
                    return fail('PLAIN_QUERY');
                const columns = ['id', 'attributes', 'participation', 'reason', 'version'];
                if (query.fields?.some(name => !columns.includes(name)))
                    return fail('PLAIN_QUERY');
                const id = identity(scope, view, { source: 'plain.actors', input });
                const descending = query.order === 'desc', last = after(request.cursor, id);
                const where = 'substr(id,1,length(?))=?' + (query.participation ? ' AND participation=?' : '');
                const params = [query.prefix ?? '', query.prefix ?? '', ...(query.participation ? [query.participation] : [])];
                const [total] = await view.query({ sql: 'SELECT count(*) AS n FROM s0_actors WHERE ' + where, parameters: params }, { maxRows: 1, maxBytes: 1024 });
                let ranks: readonly bigint[] | undefined;
                if (query.sample) {
                    const random = ctx.get('random') as RandomPort | undefined;
                    if (!random)
                        return fail('PLAIN_RANDOM_REQUIRED');
                    if (typeof query.sample.seed !== 'string')
                        return fail('PLAIN_QUERY');
                    ranks = sampleRanks(random, BigInt(String(total!.n)), query.sample.size, { run: view.runId, actor: scope.actorId, seed: query.sample.seed });
                }
                const selectedColumns = Array.from(new Set(['id', ...(query.fields ?? columns)])).join(',');
                const from = ranks ? "(SELECT " + selectedColumns + ",row_number() OVER (ORDER BY id " + (descending ? 'DESC' : 'ASC') + ") AS sample_ordinal FROM s0_actors WHERE " + where + ")" : "s0_actors";
                const filter = ranks ? (ranks.length ? 'sample_ordinal IN (' + ranks.map(() => '?').join(',') + ')' : '0') : where;
                const rows = await view.query({ sql: 'SELECT ' + selectedColumns + ' FROM ' + from + ' WHERE ' + filter + (last !== undefined ? ' AND id ' + (descending ? '<' : '>') + ' ?' : '') + ' ORDER BY id ' + (descending ? 'DESC' : 'ASC') + ' LIMIT ?', parameters: [...params, ...(ranks ?? []), ...(last !== undefined ? [last] : []), request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: Math.max(65536, request.maxResponseBytes * 2) });
                const projected = query.fields ?? columns;
                const keys = new WeakMap<object, string>();
                const items = rows.map(row => { const item = { kind: 'inline' as const, value: Object.fromEntries(Object.entries(row).filter(([key]) => projected.includes(key)).map(([key, value]) => [key, key === 'attributes' || key === 'reason' ? JSON.parse(String(value)) : typeof value === 'bigint' ? String(value) : value])) as Json }; keys.set(item, String(row.id)); return item; });
                return page(items, String(ranks?.length ?? total!.n), view.revision, request, id, item => keys.get(item));
            } });
    } };
