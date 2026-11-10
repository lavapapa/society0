import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';
import { Prompts } from '../.build/core/dist/prompts/index.js';
import { ContextMount } from '../.build/core/dist/mount-context/index.js';
import { ExplorationShell } from '../.build/core/dist/exploration-shell/index.js';
import { ActorTools, actorToolsMigration } from '../.build/core/dist/actor-tools/index.js';
import { formatToolResult } from '../.build/core/dist/actor-tools/format.js';

const targets = Array.from({ length: 7 }, (_, index) => ({ kind: 'test/item', id: 'u' + index }));
const completed = (context, value) => ({ operationId: context.operationId, status: 'completed', value, original: null, artifacts: [] });
const card = (name, extra = {}) => ({ name, inputSchema: { type: 'object' }, outputSchema: {}, effect: 'read', available: async () => true, execute: async (input, context) => completed(context, input), ...extra });

test('D27 目标与 offer 拒绝回执带说明和候选，候选最多 5 个', async (t) => {
    const env = await setup();
    const { ctx } = env;
    ctx.actions.register(card('test.free', { description: '无目标行动' }));
    ctx.actions.register(card('test.targeted', { targets: { kinds: ['test/item'], required: true }, description: '带目标行动' }));
    const dispose = ctx.actions.provide({
        id: 'd27-targets',
        actions: ['test.targeted'],
        versions: async () => [{ owner: 'd27', version: '1' }],
        offers: async () => targets.map((target) => ({ action: 'test.targeted', target })),
    });
    try {
        await t.test('无 target 的行动却给了 target', async () => {
            const receipt = await ctx.actions.execute('test.free', {}, env.context('d27-unexpected'), { kind: 'test/item', id: 'u0' });
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_TARGET_INVALID');
            assert.match(receipt.value.message, /不接受 target/);
            assert.deepEqual(receipt.value.details.actions, ['test.free']);
            assert.match(receipt.value.message, /test\.free/);
        });

        await t.test('缺少必填 target', async () => {
            const receipt = await ctx.actions.execute('test.targeted', {}, env.context('d27-missing'));
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_TARGET_INVALID');
            assert.match(receipt.value.message, /需要 target/);
            assert.match(receipt.value.message, /target\.kind 应为 test\/item/);
            assert.deepEqual(receipt.value.details.targets, targets.slice(0, 5).map((target) => target.kind + ':' + target.id));
            assert.equal(receipt.value.details.omitted, 2);
            assert.match(receipt.value.message, /另有 2 个/);
        });

        await t.test('target.kind 不符', async () => {
            const receipt = await ctx.actions.execute('test.targeted', {}, env.context('d27-kind'), { kind: 'other/kind', id: 'x' });
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_TARGET_INVALID');
            assert.match(receipt.value.message, /target\.kind 应为 test\/item/);
            assert.match(receipt.value.message, /收到 other\/kind/);
            assert.equal(receipt.value.details.targets[0], 'test/item:u0');
            assert.equal(receipt.value.details.targets.length, 5);
            assert.equal(receipt.value.details.omitted, 2);
        });

        await t.test('行动不在当前 offers', async () => {
            const receipt = await ctx.actions.execute('test.targeted', {}, env.context('d27-absent'), { kind: 'test/item', id: 'u99' });
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_NOT_OFFERED');
            assert.match(receipt.value.message, /不在可选行动里/);
            assert.equal(receipt.value.details.targets[0], 'test/item:u0');
            assert.equal(receipt.value.details.targets.length, 5);
            assert.equal(receipt.value.details.omitted, 2);
            assert.match(receipt.value.message, /另有 2 个/);
            assert.equal(receipt.value.alternatives.length, 5);
            const text = formatToolResult('act', receipt);
            assert.match(text, /ACTION_NOT_OFFERED/);
            assert.match(text, /当前可选 target/);
        });
    }
    finally {
        await dispose();
        await env.close();
    }
});

test('D27 非法 target 文本也给出原因和当前候选', async (t) => {
    const env = await setup({ migrations: [actorToolsMigration({ test: 'd27-binding' })] });
    for (const plugin of [Prompts, ContextMount, ExplorationShell, ActorTools])
        await env.host.ctx.plugin(plugin);
    let ctx;
    await env.host.ctx.plugin({ inject: ['actions', 'actorTools'], apply(c) { ctx = c; } });
    ctx.actions.register(card('test.free', { description: '无目标行动' }));
    const context = env.context('d27-binding');
    const binding = await ctx.actorTools.bind(context);
    try {
        const act = binding.value.tools.find((tool) => tool.name === 'act');
        const output = await act.execute('d27-bad-target', { action: 'test.free', target: 'not-a-target', input: {} }, context.signal);
        assert.match(output.content[0].text, /该行动不接受 target/);
        assert.match(output.content[0].text, /test\.free/);
    }
    finally {
        await binding.release();
        await env.close();
    }
});
