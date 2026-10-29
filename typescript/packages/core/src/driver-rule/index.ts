import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ handler: Schema.string().default('rule/passive/v1'), sources: Schema.array(Schema.string()).default(['actor/v1']) });
export const metadata: PluginMetadata = { "title": "driver-rule", "summary": "最小规则驱动，使用同一actions/receipt路径。", "configVersion": 1, "configPolicy": [{ "path": "/handler", "mode": "boundary", "affectsResults": true, "secret": false }, { "path": "/sources", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./driver-rule", "provides": [], "requires": [{ "service": { "key": "drivers", "protocol": "drivers", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "perceptions", "protocol": "perceptions", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "threads", "protocol": "threads", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actorEligibility", "protocol": "actorEligibility", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Context } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import { slotKey } from '@society0/host/slots';
import type { RuleHandler } from '../contracts/domain.js';
import type { Json } from '../contracts/common.js';
import type { ActorEligibility } from '../contracts/eligibility.js';
import type { PerceptionInput } from '../contracts/perception.js';
import '../drivers/index.js';
import '../perception/index.js';
import '../thread/index.js';
import { fail, equal } from '../information/page.js';
export const ruleHandlerSlot = slotKey<RuleHandler>('society/rule-handler');
export default { Config, inject: ['drivers', 'slots', 'perceptions', 'threads', 'actorEligibility'], apply(ctx: Context, config: {
        handler: string;
        sources: string[];
    }) {
        ctx.slots.declare({ key: ruleHandlerSlot, strategy: 'keyed', required: true, validate: (value: any) => {
                if (typeof value?.activate !== 'function')
                    return fail('RULE_HANDLER_INVALID');
                return value;
            } });
        ctx.drivers.register('rule', { async activate(intent, context) {
                const handler = await ctx.slots.acquire(ruleHandlerSlot, context), perceptions = await ctx.perceptions.acquire(config.sources, context);
                try {
                    const rule = handler.items.find(item => item.id === config.handler);
                    if (!rule)
                        return fail('RULE_HANDLER_MISSING');
                    let view = await context.database.snapshot({ maxAgeMs: 30000 });
                    const thread = await ctx.threads.open(intent.actorId, intent.momentId, view);
                    await view.close();
                    const requiredOperations: string[] = [], batchIds: string[] = [];
                    while (true) {
                        const batch = await perceptions.value.collect({ actorId: intent.actorId, momentId: intent.momentId, threadId: thread.id, logicalTime: context.logicalTime, reason: { source: intent.source } }, { maxItems: 100, maxResponseBytes: 65536 }, context);
                        if (!batch.reads.length)
                            break;
                        const inputs: PerceptionInput[] = [];
                        for (const read of batch.reads) {
                            if (read.page.hasMore && equal(read.page.nextCursor, read.before))
                                return fail('PERCEPTION_NO_PROGRESS');
                            if (!read.page.items.length)
                                continue;
                            const message = await ctx.threads.prepare({ id: randomUUID(), interactionId: thread.interactionId, role: 'user', content: JSON.parse(JSON.stringify(read.page.items)), originalProviderFields: { sourceId: read.sourceId, batchId: batch.id } }, context.signal);
                            inputs.push({ sourceId: read.sourceId, perceptIds: read.page.items.map(item => item.id), message, contexts: read.page.items.map(item => item.material) });
                        }
                        const operationId = context.operationId + ':perception:' + batch.id;
                        await context.database.transaction(context.writeLease, { operationId, maxDurationMs: 15000, maxStatements: 2048, maxInputBytes: 1048576, maxChangedRows: 2048, maxCapturedBytes: 1048576 }, async (tx) => {
                            context.signal.throwIfAborted();
                            const eligibility = ctx.get('actorEligibility') as ActorEligibility;
                            if (!(await eligibility.evaluate({ actorId: intent.actorId, momentId: intent.momentId, logicalTime: context.logicalTime, kind: 'activation', intentId: intent.id, driver: intent.driver }, tx)).allowed)
                                return fail('RULE_ACTOR_INELIGIBLE');
                            await perceptions.value.commit(tx, batch, inputs);
                        });
                        requiredOperations.push(operationId);
                        batchIds.push(batch.id);
                        if (!batch.reads.some(read => read.page.hasMore))
                            break;
                    }
                    const result = await rule.value.activate({ input: intent.input, threadId: thread.id, perceptionBatchIds: batchIds }, context);
                    return { ...result, requiredOperations: [...requiredOperations, ...result.requiredOperations ?? []] };
                }
                finally {
                    await perceptions.release();
                    await handler.release();
                }
            } });
    } };
