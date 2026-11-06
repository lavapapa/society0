import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';
import {STATE_SOURCE, contextBudget, DEFAULT_CONTEXT_BUDGET} from '../.build/core/dist/contracts/context.js';

async function rowsOf(ctx, actor = 'a', moment = 'm') {
    const view = await ctx.database.snapshot({maxAgeMs: 30000});
    try {
        const [thread] = await view.query({sql: 'SELECT id FROM threads WHERE actor_id=? AND moment_id=?', parameters: [actor, moment]}, {maxRows: 1, maxBytes: 1024});
        if (!thread) return [];
        const rows = await view.query({sql: 'SELECT message_id,role,payload FROM thread_messages WHERE thread_id=? ORDER BY seq', parameters: [String(thread.id)]}, {maxRows: 10000, maxBytes: 1048576});
        return rows.map(row => {
            const stored = JSON.parse(String(row.payload));
            const message = stored.kind === 'inline' ? stored.value : null;
            const native = message && message.content && typeof message.content === 'object' && !Array.isArray(message.content) && typeof message.content.role === 'string' ? message.content : message;
            return {id: String(row.message_id), role: String(row.role), value: native};
        });
    } finally {
        await view.close();
    }
}

async function activateAt(ctx, run, id, moment = 'm') {
    const context = run.context(id, 'a', moment);
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        return await driver.value.activate({id, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: id}, context);
    } finally {
        await driver.release();
    }
}

test('C3 header is a persisted SystemMessage and baseline is sent once', async t => {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}]), assistant([{type: 'text', text: 'two'}])],
        prompts: {sections: [{id: 'role', order: 0, text: 'HEADER-ROLE'}, {id: 'profile', order: 1, text: 'BASELINE-PROFILE', scope: 'baseline'}], contexts: [{id: 'frozen', text: 'frozen'}]},
        observe: messages => requests.push(messages)
    });
    t.after(() => run.close());
    assert.equal((await activateAt(ctx, run, 'c3-prefix-one')).status, 'completed');
    assert.equal((await activateAt(ctx, run, 'c3-prefix-two')).status, 'completed');
    assert.equal(requests.length, 2);
    assert.equal(requests[0][0].role, 'system');
    assert.match(requests[0][0].content, /HEADER-ROLE/);
    assert.match(requests[0][0].content, /工具使用守则/);
    assert.equal(requests[0][0].content.includes('BASELINE-PROFILE'), false);
    const firstUser = requests[0].find(message => message.role === 'user');
    assert.match(firstUser.content, /BASELINE-PROFILE/);
    assert.equal(requests[1][0].role, 'system');
    assert.equal(requests[1][0].content, requests[0][0].content);
    const secondInput = (await rowsOf(ctx)).find(row => row.id === 'c3-prefix-two/input');
    assert.equal(secondInput.value.content.includes('BASELINE-PROFILE'), false);
    assert.deepEqual(requests[1].slice(0, requests[0].length), requests[0]);
    const rows = await rowsOf(ctx);
    assert.equal(rows[0].role, 'system');
    assert.match(rows[0].value.content, /HEADER-ROLE/);
});

test('C3 state channel sends full state once and then only replaced and removed keys', async t => {
    const state = {values: {a: 1, b: 2}};
    const {run, ctx} = await driverFixture({
        messages: [0, 1, 2, 3].map(index => assistant([{type: 'text', text: 'turn-' + index}])),
        register(c) {
            c.slots.contribute(STATE_SOURCE, {id: 'demo.state', value: {id: 'demo.state', snapshot: async () => state.values}});
        }
    });
    t.after(() => run.close());
    assert.equal((await activateAt(ctx, run, 'c3-state-one')).status, 'completed');
    const one = (await rowsOf(ctx)).find(row => row.id === 'c3-state-one/input');
    assert.match(one.value.content, /当前状态/);
    assert.match(one.value.content, /^b 2$/m);
    assert.equal((await activateAt(ctx, run, 'c3-state-two')).status, 'completed');
    const two = (await rowsOf(ctx)).find(row => row.id === 'c3-state-two/input');
    assert.equal(two.value.content.includes('当前状态'), false);
    state.values = {a: 3};
    assert.equal((await activateAt(ctx, run, 'c3-state-three')).status, 'completed');
    const three = (await rowsOf(ctx)).find(row => row.id === 'c3-state-three/input');
    assert.match(three.value.content, /状态（变更）/);
    assert.match(three.value.content, /^\+ a 3$/m);
    assert.match(three.value.content, /- b/);
    assert.equal((await activateAt(ctx, run, 'c3-state-four')).status, 'completed');
    const four = (await rowsOf(ctx)).find(row => row.id === 'c3-state-four/input');
    assert.equal(four.value.content.includes('状态（变更）'), false);
});

test('C3 thread lifetime is configurable between moment and actor', async t => {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}]), assistant([{type: 'text', text: 'two'}])],
        driver: {threadLifetime: 'actor'},
        observe: messages => requests.push(messages)
    });
    t.after(() => run.close());
    assert.equal((await activateAt(ctx, run, 'c3-actor-one', 'm1')).status, 'completed');
    assert.equal((await activateAt(ctx, run, 'c3-actor-two', 'm2')).status, 'completed');
    assert.equal(requests.length, 2);
    assert.ok(requests[1].some(message => message.role === 'user' && String(message.content).includes('c3-actor-one')), JSON.stringify(requests[1]));
    const rows = await rowsOf(ctx, 'a', 'thread/actor/a');
    assert.ok(rows.some(row => row.id === 'c3-actor-one/input'));
    assert.ok(rows.some(row => row.id === 'c3-actor-two/input'));
});

test('C3 ContextBudget exposes slots and folds oversized state with a path', async t => {
    const budget = contextBudget();
    assert.equal(budget.slot('actions'), 800);
    assert.equal(budget.bytes('actions'), 2560);
    assert.equal(budget.total(), Object.values(DEFAULT_CONTEXT_BUDGET).reduce((sum, value) => sum + value, 0));
    const state = {values: {huge: 'x'.repeat(40000)}};
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}])],
        driver: {budget: {...DEFAULT_CONTEXT_BUDGET, stateDelta: 100}},
        register(c) {
            c.slots.contribute(STATE_SOURCE, {id: 'demo.state', value: {id: 'demo.state', snapshot: async () => state.values}});
        }
    });
    t.after(() => run.close());
    assert.equal((await activateAt(ctx, run, 'c3-budget-one')).status, 'completed');
    const row = (await rowsOf(ctx)).find(item => item.id === 'c3-budget-one/input');
    assert.match(row.value.content, /\/self\/state\/demo\.state/);
    assert.equal(row.value.content.includes('x'.repeat(1000)), false);
});
