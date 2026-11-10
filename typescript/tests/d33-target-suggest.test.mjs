// cases: D33 target 有界补全、TARGET_INVALID/NOT_OFFERED suggestedCall、插件 ACTION_INPUT 补 inputHelp
import test from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';
import { Prompts } from '../.build/core/dist/prompts/index.js';
import { ContextMount } from '../.build/core/dist/mount-context/index.js';
import { ExplorationShell } from '../.build/core/dist/exploration-shell/index.js';
import { ActorTools, actorToolsMigration } from '../.build/core/dist/actor-tools/index.js';
import { actionRejection } from '../.build/core/dist/actions/index.js';

const uniqueId = 'd470aed5-unique';
const ambiguousId = 'd470aed5-ambiguous';
const proposals = ['proposal-1', 'proposal-2', 'proposal-3', 'proposal-4'];

const completed = (context, value) => ({ operationId: context.operationId, status: 'completed', value, original: null, artifacts: [] });
const card = (name, extra = {}) => ({
    name,
    description: name,
    inputSchema: { type: 'object', additionalProperties: true },
    outputSchema: {},
    effect: 'read',
    available: async () => true,
    execute: async (input, context) => completed(context, { ok: true, input }),
    ...extra,
});

async function harness(t) {
    const run = await setup({ migrations: [actorToolsMigration({ test: 'd33-target-suggest' })] });
    t.after(() => run.close());
    for (const plugin of [Prompts, ContextMount, ExplorationShell, ActorTools])
        await run.host.ctx.plugin(plugin);
    let ctx;
    await run.host.ctx.plugin({
        name: 'd33-target-suggest',
        inject: ['actions', 'actorTools'],
        apply(c) {
            ctx = c;
            c.actions.register(card('econ.agenda.resolve', { targets: { kinds: ['econ/agenda', 'econ/participant'], required: true } }));
            c.actions.register(card('contract.accept', {
                targets: { kinds: ['contract/proposal'], required: true },
                inputHelp: { example: { accepted: true } },
            }));
            c.actions.register(card('contract.propose', {
                inputHelp: {
                    example: { seller: 'a', buyer: 'b', obligations: [] },
                    fieldAliases: { party: ['seller'], parties: ['seller', 'buyer'] },
                },
                execute: async (input) => {
                    if (input.keep === true)
                        throw actionRejection('ACTION_INPUT', '未知字段 keep', { example: { seller: 'custom' } });
                    if (input.unit === 'barrel')
                        throw actionRejection('ACTION_INPUT', 'contract.propose 无法识别单位 barrel；未知字段：unit；允许的字段：seller、buyer、obligations', { fieldHints: ['插件提示：unit 应为 seller'] });
                    if (input.silent === true)
                        throw actionRejection('ACTION_INPUT', '');
                    throw actionRejection('ACTION_INPUT', '未知字段 party');
                },
            }));
            c.actions.provide({
                id: 'd33-offers',
                actions: ['econ.agenda.resolve', 'contract.accept'],
                versions: async () => [{ owner: 'd33', version: '1' }],
                offers: async () => [
                    { action: 'econ.agenda.resolve', target: { kind: 'econ/agenda', id: uniqueId }, prefill: { note: 'unique' } },
                    { action: 'econ.agenda.resolve', target: { kind: 'econ/agenda', id: ambiguousId } },
                    { action: 'econ.agenda.resolve', target: { kind: 'econ/participant', id: ambiguousId } },
                    ...proposals.map((id, index) => ({ action: 'contract.accept', target: { kind: 'contract/proposal', id }, ...(index === 0 ? { prefill: { accepted: true } } : {}) })),
                ],
            });
        },
    });
    return { run, ctx };
}

async function call(binding, context, toolCallId, action, target, input = {}) {
    const act = binding.value.tools.find((tool) => tool.name === 'act');
    await act.execute(toolCallId, { action, ...(target === undefined ? {} : { target }), input }, context.signal);
    const receipts = await binding.value.actionReceipts.read();
    return receipts.find((item) => item.toolCallId === toolCallId).receipt;
}

async function withBinding(run, ctx, body) {
    const context = run.context('d33-target-suggest');
    const binding = await ctx.actorTools.bind(context);
    try {
        return await body(binding, context);
    }
    finally {
        await binding.release();
    }
}

test('D33 唯一可推出的 target 自动补全并写入 targetNormalization', async (t) => {
    const { run, ctx } = await harness(t);
    await withBinding(run, ctx, async (binding, context) => {
        await t.test('裸 id 唯一：补全为 econ/agenda:<id>', async () => {
            const receipt = await call(binding, context, 'd33-bare-id', 'econ.agenda.resolve', uniqueId);
            assert.equal(receipt.status, 'completed');
            assert.equal(receipt.value.targetNormalization, `已补全：${uniqueId}→econ/agenda:${uniqueId}`);
        });

        await t.test('仅 kind 唯一：补全为该 kind 下唯一的 target', async () => {
            const receipt = await call(binding, context, 'd33-kind', 'econ.agenda.resolve', 'econ/participant');
            assert.equal(receipt.status, 'completed');
            assert.equal(receipt.value.targetNormalization, `已补全：econ/participant→econ/participant:${ambiguousId}`);
        });
    });
});

test('D33 不能唯一推出时不补全，仍拒绝并给 suggestedCall', async (t) => {
    const { run, ctx } = await harness(t);
    await withBinding(run, ctx, async (binding, context) => {
        await t.test('裸 id 命中两个当前 target：保持 ACTION_TARGET_INVALID', async () => {
            const receipt = await call(binding, context, 'd33-ambiguous-id', 'econ.agenda.resolve', ambiguousId);
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_TARGET_INVALID');
            assert.equal(receipt.value.targetNormalization, undefined);
            assert.equal(receipt.value.suggestedCall.length, 3);
            assert.deepEqual(receipt.value.suggestedCall.map((item) => item.target), [
                `econ/agenda:${ambiguousId}`,
                `econ/agenda:${uniqueId}`,
                `econ/participant:${ambiguousId}`,
            ]);
            assert.ok(receipt.value.suggestedCall.every((item) => item.act === 'econ.agenda.resolve' && item.target.includes(':')));
        });

        await t.test('行动名被当作 target：不补全并可在建议里抄到完整 target', async () => {
            const receipt = await call(binding, context, 'd33-action-as-target', 'econ.agenda.resolve', 'agenda.resolve');
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_TARGET_INVALID');
            assert.equal(receipt.value.targetNormalization, undefined);
            assert.equal(receipt.value.suggestedCall.length, 3);
            assert.ok(receipt.value.suggestedCall.every((item) => item.target.startsWith('econ/agenda:') || item.target.startsWith('econ/participant:')));
        });

        await t.test('对不接受 target 的行动传 target：建议不带 target 并说明原因', async () => {
            const receipt = await call(binding, context, 'd33-unexpected', 'plain.update', `econ/agenda:${uniqueId}`);
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_TARGET_INVALID');
            assert.equal(receipt.value.suggestedCall.length, 1);
            assert.equal(receipt.value.suggestedCall[0].act, 'plain.update');
            assert.equal(receipt.value.suggestedCall[0].target, undefined);
            assert.equal(receipt.value.suggestedCall[0].note, '此行动不需要 target');
        });

        await t.test('对象选错：ACTION_NOT_OFFERED 的 suggestedCall 最多 3 条且带 prefill/example', async () => {
            const receipt = await call(binding, context, 'd33-wrong-object', 'contract.accept', 'contract/proposal:missing');
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'ACTION_NOT_OFFERED');
            assert.equal(receipt.value.suggestedCall.length, 3);
            assert.ok(receipt.value.suggestedCall.every((item) => item.act === 'contract.accept' && item.target.startsWith('contract/proposal:')));
            assert.deepEqual(receipt.value.suggestedCall[0].prefill, { accepted: true });
            assert.deepEqual(receipt.value.suggestedCall[0].example, { accepted: true });
        });
    });
});

test('D33 插件 handler 抛出的 ACTION_INPUT 由 Core 补 inputHelp', async (t) => {
    const { run, ctx } = await harness(t);
    await withBinding(run, ctx, async (binding, context) => {
        const receipt = await call(binding, context, 'd33-plugin-input', 'contract.propose', undefined, { party: 'a' });
        assert.equal(receipt.status, 'rejected');
        assert.equal(receipt.value.code, 'ACTION_INPUT');
        assert.equal(receipt.value.action, 'contract.propose');
        assert.deepEqual(receipt.value.example, { seller: 'a', buyer: 'b', obligations: [] });
        assert.equal(receipt.value.exampleText, '{"buyer":"b","obligations":[],"seller":"a"}');
        assert.deepEqual(receipt.value.fieldHints, ['你写了 party，应为 seller']);
        assert.match(receipt.value.message, /^未知字段 party/);
        assert.match(receipt.value.message, /请参考 example 与 fieldHints/);

        const specific = await call(binding, context, 'd33-plugin-specific-message', 'contract.propose', undefined, { unit: 'barrel', party: 'a' });
        assert.equal(specific.value.code, 'ACTION_INPUT');
        assert.match(specific.value.message, /^contract\.propose 无法识别单位 barrel；未知字段：unit；允许的字段：seller、buyer、obligations/);
        assert.match(specific.value.message, /请参考 example 与 fieldHints/);
        assert.deepEqual(specific.value.example, { seller: 'a', buyer: 'b', obligations: [] });
        assert.deepEqual(specific.value.fieldHints, ['插件提示：unit 应为 seller', '你写了 party，应为 seller']);
        assert.ok(Buffer.byteLength(specific.value.fieldHints.join('\n')) <= 2048);

        const silent = await call(binding, context, 'd33-plugin-no-message', 'contract.propose', undefined, { silent: true });
        assert.equal(silent.value.code, 'ACTION_INPUT');
        assert.equal(silent.value.message, '输入不符合 contract.propose 的 input schema；请按 example 与 fieldHints 改正后重试。');
        assert.deepEqual(silent.value.example, { seller: 'a', buyer: 'b', obligations: [] });

        const kept = await call(binding, context, 'd33-plugin-input-kept', 'contract.propose', undefined, { keep: true });
        assert.equal(kept.value.code, 'ACTION_INPUT');
        assert.deepEqual(kept.value.details.example, { seller: 'custom' });
        assert.equal(kept.value.example, undefined, '已带 example 时不覆盖');
        assert.equal(kept.value.message, '未知字段 keep');
    });
});
