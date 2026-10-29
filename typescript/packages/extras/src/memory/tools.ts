import type { Context } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { ActionDefinition, ActionReceipt } from '@society0/core/contracts/domain';
import type { Json } from '@society0/core/contracts/common';
import type { Experience } from '../memory-repository/index.js';
const readInput = Schema.object({ text: Schema.string(), maxItems: Schema.natural().min(1).max(100).default(10) });
const writeInput = Schema.object({ threadId: Schema.string().required(), body: Schema.any<Json>().required() });
function receipt(operationId: string, value: Json): ActionReceipt { return { operationId, status: 'completed', value, original: value, artifacts: [] }; }
export function memoryActions(ctx: Context): readonly ActionDefinition[] {
    return [
        { name: 'memory.recall', effect: 'read', inputSchema: { type: 'object', properties: { text: { type: 'string' }, maxItems: { type: 'integer', minimum: 1, maximum: 100 } }, additionalProperties: false }, outputSchema: { type: 'object' }, available: async () => true, async execute(input, context) { const options = Schema.resolve(input, readInput, {})[0]; const view = await context.database.snapshot({ maxAgeMs: 30000 }); try {
                const found = await ctx.memoryRepository.scan(view, { actorId: context.actorId, text: options.text, maxItems: options.maxItems, maxResponseBytes: 65536 });
                return receipt(context.operationId, { items: found.items.map(item => ({ id: item.id, actorId: item.actorId, threadId: item.threadId, body: item.body })), total: found.total, revision: found.revision });
            }
            finally {
                await view.close();
            } } },
        { name: 'memory.write', effect: 'world', inputSchema: { type: 'object', properties: { threadId: { type: 'string' }, body: {} }, required: ['threadId', 'body'], additionalProperties: false }, outputSchema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] }, available: async () => true, async execute(input, context) { const options = Schema.resolve(input, writeInput, {})[0]; const value: Experience = { id: context.operationId, actorId: context.actorId, threadId: options.threadId, body: options.body, artifacts: [] }; await context.transaction({ operationId: context.operationId + ':memory', maxDurationMs: 10000, maxStatements: 16, maxInputBytes: 65536, maxChangedRows: 8, maxCapturedBytes: 131072 }, tx => ctx.memoryRepository.append(tx, value)); return receipt(context.operationId, { id: value.id }); } }
    ];
}
