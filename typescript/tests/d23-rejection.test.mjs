// cases: D23 domain rule rejection settles as rejected, not tool_failed; unmarked failures and infrastructure codes stay incomplete
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readFile, writeFile } from 'node:fs/promises';
import { setup } from './pr07-support.mjs';
import { assistant, extensions } from './pr08-support.mjs';
import { PiRuntime } from '../.build/core/dist/agent-runtime-pi/runtime.js';
import { OrderedInferenceSink } from '../.build/core/dist/agent-runtime-pi/sink.js';
import { ReplayProvider } from '../.build/core/dist/model-replay/provider.js';
import { Prompts } from '../.build/core/dist/prompts/index.js';
import { ContextMount } from '../.build/core/dist/mount-context/index.js';
import { ExplorationShell } from '../.build/core/dist/exploration-shell/index.js';
import { ActorTools, actorToolsMigration } from '../.build/core/dist/actor-tools/index.js';
import { actionRejection } from '../.build/core/dist/actions/index.js';
import { call, cli, fixture, flatten, json, localExperiment, skip as buildSkip, socialReplay, waitFor, worldFile } from './d7-support.mjs';

const enginePackages = ['@society0/host', '@society0/core', '@society0/extras', '@society0/social'];
const requireE2E = process.env.SOCIETY0_REQUIRE_E2E === '1';
const skip = requireE2E ? false : buildSkip;

async function harness(t, register) {
    const run = await setup({ migrations: [actorToolsMigration({ test: 'd23-rejection' })] });
    t.after(() => run.close());
    for (const plugin of [Prompts, ContextMount, ExplorationShell, ActorTools]) await run.host.ctx.plugin(plugin);
    let ctx;
    await run.host.ctx.plugin({ inject: ['actions', 'actorTools'], apply(c) { ctx = c; register?.(c) } });
    const context = run.context('d23-rejection');
    const binding = await ctx.actorTools.bind(context);
    return { run, ctx, context, binding };
}
const invokeTool = binding => binding.value.tools.find(tool => tool.name === 'act');
async function runModel(t, { context, binding, tools, fixture, requiredActions = [], maxRequests = 3 }) {
    const runtime = new PiRuntime();
    t.after(() => runtime.drain('d23 fixture'));
    const provider = new ReplayProvider({ fixture });
    t.after(() => provider.close());
    const held = await provider.prepare({ provider: 'replay/v1', model: 'fixture' }, context);
    t.after(() => held.release());
    const sink = new OrderedInferenceSink({ maxPendingEvents: 64, maxPendingBytes: 1048576 }, async () => {}, async () => {}, () => {});
    return runtime.run({ threadId: 'd23-thread', interactionId: 'd23-interaction', history: [], prompts: [{ role: 'user', content: 'act', timestamp: 0 }], tools, actionReceipts: binding.value.actionReceipts, selection: held.value, budget: { maxRequests, maxToolCalls: 4 }, requiredActions }, context, sink, extensions());
}
const toolTurn = (id, action, input = {}) => assistant([{ type: 'toolCall', id, name: 'act', arguments: { action, input } }], 'toolUse');
const finalTurn = assistant([{ type: 'text', text: 'done' }]);

function ruleActions(c) {
    c.actions.register({
        name: 'fixture.rule', effect: 'read', exposure: 'tool', inputSchema: { type: 'object' }, outputSchema: {}, available: async () => true,
        execute: async () => { throw actionRejection('ECON_BUDGET_INVALID', '资金不足，无法支付本次行动', { need: 10, have: 7 }) },
    });
    c.actions.register({
        name: 'fixture.good', effect: 'read', exposure: 'tool', inputSchema: { type: 'object' }, outputSchema: {}, available: async () => true,
        execute: async (input, context) => ({ operationId: context.operationId, status: 'completed', value: { ok: true }, original: input, artifacts: [] }),
    });
}

test('D23 actionRejection settles activation success with ACTION_REJECTED and actRejected count', async t => {
    const h = await harness(t, ruleActions);
    try {
        const outcome = await runModel(t, { ...h, tools: [invokeTool(h.binding)], fixture: [toolTurn('reject-1', 'fixture.rule'), toolTurn('good-1', 'fixture.good'), finalTurn] });
        assert.equal(outcome.status, 'completed', JSON.stringify(outcome));
        assert.equal(outcome.reason, 'success', JSON.stringify(outcome));
        assert.equal(outcome.actRejected, 1, JSON.stringify(outcome));
        assert.equal(outcome.actFailed, 0, JSON.stringify(outcome));
        assert.ok(outcome.warnings?.includes('ACTION_REJECTED'), JSON.stringify(outcome));
        const rejected = outcome.actionReceipts.find(item => item.action === 'fixture.rule');
        const good = outcome.actionReceipts.find(item => item.action === 'fixture.good');
        assert.equal(rejected.receipt.status, 'rejected', JSON.stringify(rejected));
        assert.deepEqual(rejected.receipt.value, { code: 'ECON_BUDGET_INVALID', message: '资金不足，无法支付本次行动', details: { need: 10, have: 7 }, action: 'fixture.rule' });
        assert.equal(good.receipt.status, 'completed', JSON.stringify(good));
    } finally {
        await h.binding.release();
    }
});

test('D23 rejected receipt content is visible in the tool result text', async t => {
    const h = await harness(t, ruleActions);
    try {
        const result = await invokeTool(h.binding).execute('visible-1', { action: 'fixture.rule', input: {} });
        assert.match(result.content[0].text, /ECON_BUDGET_INVALID/, result.content[0].text);
        assert.match(result.content[0].text, /资金不足/, result.content[0].text);
        assert.equal(result.details.status, 'rejected', JSON.stringify(result));
        assert.equal(result.details.code, 'ECON_BUDGET_INVALID', JSON.stringify(result));
    } finally {
        await h.binding.release();
    }
});

test('D23 unmarked execute throw remains tool_failed', async t => {
    const h = await harness(t, c => {
        c.actions.register({ name: 'fixture.broken', effect: 'read', exposure: 'tool', inputSchema: { type: 'object' }, outputSchema: {}, available: async () => true, execute: async () => { throw Error('plugin exploded') } });
    });
    try {
        const outcome = await runModel(t, { ...h, tools: [invokeTool(h.binding)], fixture: [toolTurn('broken-1', 'fixture.broken'), finalTurn] });
        assert.equal(outcome.status, 'incomplete', JSON.stringify(outcome));
        assert.equal(outcome.reason, 'tool_failed', JSON.stringify(outcome));
        assert.equal(outcome.actFailed, 1, JSON.stringify(outcome));
        assert.equal(outcome.actRejected, 0, JSON.stringify(outcome));
        assert.equal(outcome.warnings?.includes('ACTION_REJECTED') ?? false, false, JSON.stringify(outcome));
    } finally {
        await h.binding.release();
    }
});

test('D23 infrastructure rejection code still stays incomplete', async t => {
    const h = await harness(t, c => {
        c.actions.register({ name: 'fixture.infra', effect: 'read', exposure: 'tool', inputSchema: { type: 'object' }, outputSchema: {}, available: async () => true, execute: async () => { throw actionRejection('TOOL_CALL_BUDGET', '工具调用额度已耗尽') } });
    });
    try {
        const outcome = await runModel(t, { ...h, tools: [invokeTool(h.binding)], fixture: [toolTurn('infra-1', 'fixture.infra'), finalTurn] });
        assert.equal(outcome.status, 'incomplete', JSON.stringify(outcome));
        assert.equal(outcome.reason, 'tool_failed', JSON.stringify(outcome));
        assert.equal(outcome.actFailed, 1, JSON.stringify(outcome));
        assert.equal(outcome.actRejected, 0, JSON.stringify(outcome));
    } finally {
        await h.binding.release();
    }
});

test('D23 rejected action with unsatisfied required action remains incomplete', async t => {
    const h = await harness(t, ruleActions);
    try {
        const outcome = await runModel(t, {
            ...h,
            tools: [invokeTool(h.binding)],
            requiredActions: [{ name: 'fixture.good', minimumCompleted: 1 }],
            fixture: [toolTurn('reject-2', 'fixture.rule'), finalTurn, finalTurn],
            maxRequests: 4,
        });
        assert.equal(outcome.status, 'incomplete', JSON.stringify(outcome));
        assert.equal(outcome.reason, 'required_action_failed', JSON.stringify(outcome));
        assert.equal(outcome.requiredSatisfied, false, JSON.stringify(outcome));
        assert.equal(outcome.actRejected, 1, JSON.stringify(outcome));
        assert.equal(outcome.actFailed, 0, JSON.stringify(outcome));
    } finally {
        await h.binding.release();
    }
});

test('D23 execute-returned rejected receipt with non-whitelisted code settles as domain rejection', async t => {
    const h = await harness(t, c => {
        c.actions.register({
            name: 'fixture.returned', effect: 'read', exposure: 'tool', inputSchema: { type: 'object' }, outputSchema: {}, available: async () => true,
            execute: async (input, context) => ({ operationId: context.operationId, status: 'rejected', value: { code: 'ECON_STATE_INVALID', message: '当前状态不允许该行动' }, original: null, artifacts: [] }),
        });
    });
    try {
        const outcome = await runModel(t, { ...h, tools: [invokeTool(h.binding)], fixture: [toolTurn('returned-1', 'fixture.returned'), finalTurn] });
        assert.equal(outcome.status, 'completed', JSON.stringify(outcome));
        assert.equal(outcome.reason, 'success', JSON.stringify(outcome));
        assert.equal(outcome.actRejected, 1, JSON.stringify(outcome));
        assert.equal(outcome.actFailed, 0, JSON.stringify(outcome));
        assert.ok(outcome.warnings?.includes('ACTION_REJECTED'), JSON.stringify(outcome));
        assert.equal(outcome.actionReceipts[0].receipt.value.code, 'ECON_STATE_INVALID', JSON.stringify(outcome.actionReceipts[0]));
    } finally {
        await h.binding.release();
    }
});

const rejectionPluginSource = `import { actionRejection } from '@society0/core/actions';
export default {
    inject: ['actions'],
    apply(ctx) {
        ctx.actions.register({
            name: 'fixture.reject',
            effect: 'read',
            exposure: 'tool',
            inputSchema: { type: 'object' },
            outputSchema: {},
            available: async () => true,
            execute: async () => { throw actionRejection('ECON_CONTRACT_TERMS', '规则拒绝：当前契约不允许该行动', { rule: 'd23' }); },
        });
    },
};
`;

test('D23 social replay tick settles a rule rejection, publishes a complete point, and resumes without HISTORY_DIRTY', { skip, timeout: 300_000 }, async () => {
    assert.ok(existsSync(cli), 'BUILD_REQUIRED: 先运行 python3 -B scripts/build.py 生成 .build/cli');
    const { directory, profile } = await fixture('social-sqlite', value => {
        socialReplay(value);
        const entries = flatten(value.patches[0].insert);
        const replay = entries.find(entry => entry.id === 'replay');
        replay.config.fixtures['social-replay'] = [
            assistant([{ type: 'toolCall', id: 'd23-reject-1', name: 'act', arguments: { action: 'fixture.reject', input: {} } }], 'toolUse'),
            assistant([{ type: 'toolCall', id: 'd23-publish-1', name: 'act', arguments: { action: 'social.publish', input: { content: 'd23 rejection post' } } }], 'toolUse'),
            assistant([{ type: 'text', text: 'done' }]),
        ];
        const generation = entries.find(entry => entry.id === 'generation');
        generation.config.push({ id: 'd23-reject', name: '@fixture/local-plugin', config: {} });
    });
    const local = await localExperiment(directory, '@fixture/local-plugin', '1.0.0', { scope: 'generation', source: rejectionPluginSource });
    const built = JSON.parse(await readFile(profile, 'utf8'));
    built.lockfile = pathToFileURL(local.lock).href;
    await writeFile(profile, JSON.stringify(built));
    const locked = call(['lock', '--base', local.exp, '--out', local.lock, local.name, ...enginePackages, '--json']);
    assert.equal(locked.status, 0, locked.stderr);
    const world = await worldFile(directory);

    const runDir = join(directory, 'run-d23-rejection');
    const started = json(call(['run', 'start', '--profile', profile, '--base', local.exp, '--dir', runDir, '--world', world, '--ticks', '1', '--detach', '--json']));
    assert.equal(started.schema, 'society0.run/1');
    const status = await waitFor(runDir, value => value.state === 'completed' || value.state === 'failed');
    assert.equal(status.state, 'completed', JSON.stringify(status));
    assert.equal(status.completedTicks, 1, JSON.stringify(status));

    const result = json(call(['run', 'result', '--dir', runDir, '--json']));
    assert.equal(result.result.status, 'completed', JSON.stringify(result.result));
    assert.equal(result.result.cut.frontier.completedCount, '1', JSON.stringify(result.result.cut));
    assert.equal(result.activations.failed, 0, JSON.stringify(result.activations));
    const entries = result.result.activations.entries;
    const rejected = entries.find(entry => entry.rejected === 1);
    assert.ok(rejected, JSON.stringify(entries));
    assert.equal(rejected.reason, 'success', JSON.stringify(rejected));
    assert.equal(rejected.requiredSatisfied, true, JSON.stringify(rejected));
    assert.ok(rejected.warnings.includes('ACTION_REJECTED'), JSON.stringify(rejected));

    const resumed = call(['run', 'resume', '--dir', runDir, '--detach', '--json']);
    assert.equal(resumed.status, 1, resumed.stdout);
    assert.doesNotMatch(resumed.stderr, /HISTORY_DIRTY/);
    assert.equal(JSON.parse(resumed.stderr).code, 'RUN_COMPLETED', resumed.stderr);
});
