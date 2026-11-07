import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ initialActors: Schema.array(Schema.object({ id: Schema.string().required(), attributes: Schema.any().required() })).default([]) });
export const metadata: PluginMetadata = { "title": "plain-init", "summary": "Core包的初始化入口，在未公开run内写入配置的主体。", "configVersion": 1, "configPolicy": [{ "path": "/initialActors", "mode": "new-run", "affectsResults": true, "secret": false }], "scope": "bootstrap", "allowedScopes": ["bootstrap"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./plain-init", "provides": [], "requires": [{ "service": { "key": "initializers", "protocol": "initializers", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actors", "protocol": "actors", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/cordis';
import type { Actor } from '../actors/index.js';
import { ensureActorsTable, writePublicActor } from '../plain/public.js';
import '../initializers/index.js';
import '../actors/index.js';
export default { Config, inject: ['initializers', 'actors'], apply(ctx: Context, config: {
        initialActors: Actor[];
    }) {
        ensureActorsTable(ctx);
        ctx.initializers.register({ id: 'plain.seed', after: [], async initialize(context) {
                const operations: string[] = [];
                for (let offset = 0; offset < config.initialActors.length; offset += 64) {
                    context.signal.throwIfAborted();
                    const operationId = 'plain.seed:' + context.runId + ':' + offset;
                    await context.database.transaction(context.writeLease, { operationId, maxDurationMs: 15000, maxStatements: 128, maxInputBytes: 1048576, maxChangedRows: 128, maxCapturedBytes: 1048576 }, async (tx) => {
                        for (const actor of config.initialActors.slice(offset, offset + 64)) {
                            await ctx.actors.create(tx, actor);
                            const state = await ctx.actors.state(actor.id, tx);
                            if (state)
                                await writePublicActor(ctx, tx, { id: actor.id, participation: state.participation, version: state.version, attributes: actor.attributes });
                        }
                    });
                    operations.push(operationId);
                }
                return { operationIds: operations };
            } });
    } };
