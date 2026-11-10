// cases: D32 声明式 inputHelp 经真实 actor-tools → act 路径进入 ACTION_INPUT 回执
import test from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';
import { assistant, extensions } from './pr08-support.mjs';
import { PiRuntime } from '../.build/core/dist/agent-runtime-pi/runtime.js';
import { OrderedInferenceSink } from '../.build/core/dist/agent-runtime-pi/sink.js';
import { ReplayProvider } from '../.build/core/dist/model-replay/provider.js';
import { Prompts } from '../.build/core/dist/prompts/index.js';
import { ContextMount } from '../.build/core/dist/mount-context/index.js';
import { ExplorationShell } from '../.build/core/dist/exploration-shell/index.js';
import { ActorTools, actorToolsMigration } from '../.build/core/dist/actor-tools/index.js';

const contractSchema = {
    type: 'object',
    properties: {
        seller: { type: 'string' },
        buyer: { type: 'string' },
        obligations: { type: 'array', items: { type: 'object' } },
    },
    required: ['seller', 'buyer', 'obligations'],
    additionalProperties: false,
};

async function harness(t, register) {
    const run = await setup({ migrations: [actorToolsMigration({ test: 'd32-input-help' })] });
    t.after(() => run.close());
    for (const plugin of [Prompts, ContextMount, ExplorationShell, ActorTools])
        await run.host.ctx.plugin(plugin);
    let ctx;
    await run.host.ctx.plugin({ name: 'd32-input-help', inject: ['actions', 'actorTools'], apply(c) { ctx = c; register(c); } });
    return { run, ctx };
}

async function act(ctx, run, action, input) {
    const context = run.context('d32-input-help');
    const binding = await ctx.actorTools.bind(context);
    const runtime = new PiRuntime();
    const provider = new ReplayProvider({
        fixture: [
            assistant([{ type: 'toolCall', id: 'd32-act', name: 'act', arguments: { action, input } }], 'toolUse'),
            assistant([{ type: 'text', text: 'done' }]),
        ],
    });
    let held;
    try {
        held = await provider.prepare({ provider: 'replay/v1', model: 'fixture' }, context);
        const sink = new OrderedInferenceSink({ maxPendingEvents: 64, maxPendingBytes: 1048576 }, async () => { }, async () => { }, () => { });
        const outcome = await runtime.run({
            threadId: 'd32-thread', interactionId: 'd32-interaction', history: [],
            prompts: [{ role: 'user', content: 'act', timestamp: 0 }],
            tools: binding.value.tools, actionReceipts: binding.value.actionReceipts,
            selection: held.value, budget: { maxRequests: 2, maxToolCalls: 2 }, requiredActions: [],
        }, context, sink, extensions());
        return outcome;
    }
    finally {
        await held?.release();
        await provider.close();
        await runtime.drain('d32');
        await binding.release();
    }
}

test('D32 有 inputHelp 的行动：真实 act 调用的 ACTION_INPUT 回执含 example 与 fieldHints', async t => {
    const { run, ctx } = await harness(t, c => {
        c.actions.register({
            name: 'contract.propose', description: '提出合同', effect: 'read',
            inputSchema: contractSchema, outputSchema: {},
            inputHelp: {
                example: { seller: 'a', buyer: 'b', obligations: [] },
                fieldAliases: { parties: ['seller', 'buyer'], type: ['obligations'], terms: ['obligations'] },
            },
            available: async () => true,
            execute: async (input, context) => ({ operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: [] }),
        });
    });
    const outcome = await act(ctx, run, 'contract.propose', { parties: ['a', 'b'], type: 'sale', terms: [] });
    assert.equal(outcome.actionReceipts.length, 1);
    const receipt = outcome.actionReceipts[0].receipt;
    assert.equal(receipt.status, 'rejected');
    assert.equal(receipt.value.code, 'ACTION_INPUT');
    assert.deepEqual(receipt.value.example, { seller: 'a', buyer: 'b', obligations: [] });
    assert.equal(receipt.value.exampleText, '{"buyer":"b","obligations":[],"seller":"a"}');
    assert.equal(receipt.value.action, 'contract.propose');
    assert.deepEqual(receipt.value.fieldHints, [
        '你写了 parties，应为 seller、buyer',
        '你写了 type，应为 obligations',
        '你写了 terms，应为 obligations',
    ]);
    assert.match(receipt.value.message, /contract\.propose/);
});

test('D32 没有 inputHelp 的行动：ACTION_INPUT 回执形状不变', async t => {
    const { run, ctx } = await harness(t, c => {
        c.actions.register({
            name: 'plain.publish', description: '发布', effect: 'read',
            inputSchema: contractSchema, outputSchema: {},
            available: async () => true,
            execute: async (input, context) => ({ operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: [] }),
        });
    });
    const outcome = await act(ctx, run, 'plain.publish', { parties: ['a', 'b'] });
    const value = outcome.actionReceipts[0].receipt.value;
    assert.equal(value.code, 'ACTION_INPUT');
    assert.deepEqual(Object.keys(value).sort(), ['code', 'errorCount', 'errors', 'expected']);
});

test('D32 fieldHints 超限只截断提示，不丢 example', async t => {
    const { run, ctx } = await harness(t, c => {
        const aliases = {};
        for (let index = 0; index < 200; index++)
            aliases['wrong' + index] = ['target.' + 'x'.repeat(40) + index];
        c.actions.register({
            name: 'many.fields', description: '多字段', effect: 'read',
            inputSchema: { type: 'object', properties: { required: { type: 'string' } }, required: ['required'] },
            outputSchema: {},
            inputHelp: { example: { required: 'ok' }, fieldAliases: aliases },
            available: async () => true,
            execute: async (input, context) => ({ operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: [] }),
        });
    });
    const input = {};
    for (let index = 0; index < 200; index++)
        input['wrong' + index] = 'x';
    const outcome = await act(ctx, run, 'many.fields', input);
    const value = outcome.actionReceipts[0].receipt.value;
    assert.ok(value.fieldHints.length > 0);
    assert.ok(value.fieldHints.length < 200, 'fieldHints 必须按字节上限截断');
    assert.deepEqual(value.example, { required: 'ok' }, 'example 不能因 fieldHints 超限丢失');
    assert.ok(Buffer.byteLength(value.fieldHints.join('\n')) <= 2048);
});
