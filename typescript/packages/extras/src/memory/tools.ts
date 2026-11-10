import type { Context } from '@society0/cordis';
import Schema from '@society0/schemastery';
import type { ActionDefinition, ActionReceipt } from '@society0/core/contracts/domain';
import type { Json } from '@society0/core/contracts/common';
import { actionRejection } from '@society0/core/actions';
import '@society0/core/thread';
import type { Experience } from '../memory-repository/index.js';
const readInput = Schema.object({ text: Schema.string(), maxItems: Schema.natural().min(1).max(100).default(10) });
const writeInput = Schema.object({ threadId: Schema.string(), body: Schema.any<Json>().required() });
function receipt(operationId: string, value: Json): ActionReceipt { return { operationId, status: 'completed', value, original: value, artifacts: [] }; }
/** 省略 threadId 时落到当前激活的 thread；传入不存在的 id 时明确拒绝，不改写。 */
async function resolveThread(ctx: Context, context: Parameters<ActionDefinition['execute']>[1], requested: string | undefined): Promise<string> {
    const view = await context.database.snapshot({ maxAgeMs: 30000 });
    try {
        const current = await (ctx.get('threads') as Context['threads']).open(context.actorId, context.momentId, view);
        if (requested === undefined)
            return current.id;
        const rows = await view.query({ sql: 'SELECT id FROM threads WHERE actor_id=? ORDER BY id LIMIT ?', parameters: [context.actorId, context.database.maxQueryRows] }, { maxRows: context.database.maxQueryRows, maxBytes: Math.min(65536, context.database.maxQueryBytes) });
        const available = [...new Set([current.id, ...rows.map(row => String(row.id))])];
        if (available.includes(requested))
            return requested;
        throw actionRejection('MEMORY_THREAD_UNKNOWN', `你传了 ${requested}，当前可用 threadId：${available.join('、') || '（无）'}`, { requested, available });
    }
    finally {
        await view.close();
    }
}
export function memoryActions(ctx: Context): readonly ActionDefinition[] {
    return [
        { name: 'memory.recall', effect: 'read', inputSchema: { type: 'object', properties: { text: { type: 'string' }, maxItems: { type: 'integer', minimum: 1, maximum: 100 } }, additionalProperties: false }, outputSchema: { type: 'object' }, available: async () => true, async execute(input, context) { const options = Schema.resolve(input, readInput, {})[0]; const view = await context.database.snapshot({ maxAgeMs: 30000 }); try {
                const found = await ctx.memoryRepository.scan(view, { actorId: context.actorId, text: options.text, maxItems: options.maxItems, maxResponseBytes: 65536 });
                return receipt(context.operationId, { items: found.items.map(item => ({ id: item.id, actorId: item.actorId, threadId: item.threadId, body: item.body })), total: found.total, revision: found.revision });
            }
            finally {
                await view.close();
            } } },
        { name: 'memory.write', effect: 'world', inputSchema: { type: 'object', properties: { threadId: { type: 'string', description: '省略则写入当前激活的 thread' }, body: {} }, required: ['body'], additionalProperties: false }, outputSchema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] }, available: async () => true, async execute(input, context) { const options = Schema.resolve(input, writeInput, {})[0]; const threadId = await resolveThread(ctx, context, options.threadId); const value: Experience = { id: context.operationId, actorId: context.actorId, threadId, body: options.body, artifacts: [] }; try { await context.transaction({ operationId: context.operationId + ':memory', maxDurationMs: 10000, maxStatements: 16, maxInputBytes: 65536, maxChangedRows: 8, maxCapturedBytes: 131072 }, tx => ctx.memoryRepository.append(tx, value)); } catch (error) { if ((error as { code?: string }).code === 'MEMORY_THREAD_IDENTITY') throw actionRejection('MEMORY_THREAD_UNKNOWN', `你传了 ${threadId}，但该 thread 不属于当前 actor 或刚刚被移除；请省略 threadId 或使用当前激活的 threadId。`); throw error; } return receipt(context.operationId, { id: value.id }); } }
    ];
}
