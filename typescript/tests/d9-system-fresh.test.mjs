import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';

const percept = () => ({
    id: 'd9/perception',
    schema: {},
    cursorScope: 'actor',
    available: async () => true,
    versions: async () => [{owner: 'd9/perception', version: '1'}],
    collect: async (scope, before) => ({
        items: before === null ? [{id: 'd9-percept-1', kind: 'notice', material: {kind: 'inline', value: {text: 'percept body'}}, provenance: {source: 'd9/perception'}}] : [],
        nextCursor: 1, through: 1, hasMore: false,
    }),
});

async function activateAt(ctx, run, id, moment = 'm') {
    const context = run.context(id, 'a', moment);
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        return await driver.value.activate({id, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: id}, context);
    } finally {
        await driver.release();
    }
}

async function rowsOf(ctx, moment = 'm') {
    const view = await ctx.database.snapshot({maxAgeMs: 30000});
    try {
        const [thread] = await view.query({sql: 'SELECT id FROM threads WHERE actor_id=? AND moment_id=?', parameters: ['a', moment]}, {maxRows: 1, maxBytes: 1024});
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

const sections = [
    {id: 'role', order: 0, text: 'D9-HEADER-ROLE'},
    {id: 'profile', order: 1, text: 'D9-BASELINE-PROFILE', scope: 'baseline'},
];

async function fixture(driver = {}, withPerception = true) {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}]), assistant([{type: 'text', text: 'two'}])],
        prompts: {sections},
        driver: {perceptions: withPerception ? ['d9/perception'] : [], ...driver},
        register(c) { if (withPerception) c.perceptions.register(percept()); },
        observe: messages => requests.push(messages),
    });
    return {run, ctx, requests};
}

test('D9 perception messages do not suppress the first SystemMessage header and baseline', async t => {
    const {run, ctx, requests} = await fixture();
    t.after(() => run.close());
    assert.equal((await activateAt(ctx, run, 'd9-fresh-one')).status, 'completed');
    assert.equal(requests.length, 1);
    assert.equal(requests[0][0].role, 'system');
    assert.match(requests[0][0].content, /D9-HEADER-ROLE/);
    assert.match(requests[0][0].content, /工具使用守则/);
    const firstUser = requests[0].find(message => message.role === 'user');
    assert.ok(firstUser);
    assert.ok(requests[0].some(message => message.role === 'user' && String(message.content).includes('D9-BASELINE-PROFILE')));
    const rows = await rowsOf(ctx);
    assert.equal(rows.filter(row => row.role === 'system' && !row.id.includes('/event/')).length, 1);
    assert.equal(rows[0].role, 'system');
    assert.match(rows[0].value.content, /D9-HEADER-ROLE/);
});

test('D9 a second activation on the same moment thread does not repeat system or baseline', async t => {
    const {run, ctx, requests} = await fixture();
    t.after(() => run.close());
    assert.equal((await activateAt(ctx, run, 'd9-fresh-one')).status, 'completed');
    assert.equal((await activateAt(ctx, run, 'd9-fresh-two')).status, 'completed');
    assert.equal(requests.length, 2);
    assert.equal(requests[1][0].role, 'system');
    assert.equal(requests[1][0].content, requests[0][0].content);
    const rows = await rowsOf(ctx);
    assert.equal(rows.filter(row => row.role === 'system' && !row.id.includes('/event/')).length, 1);
    const secondInput = rows.find(row => row.id === 'd9-fresh-two/input');
    assert.ok(secondInput);
    assert.equal(secondInput.value.content.includes('D9-BASELINE-PROFILE'), false);
});

test('D9 actor thread lifetime sends system and baseline only once across moments', async t => {
    const {run, ctx, requests} = await fixture({threadLifetime: 'actor'}, false);
    t.after(() => run.close());
    const first = await activateAt(ctx, run, 'd9-actor-one', 'm1');
    assert.equal(first.status, 'completed', JSON.stringify(first));
    const second = await activateAt(ctx, run, 'd9-actor-two', 'm2');
    assert.equal(second.status, 'completed', JSON.stringify(second));
    assert.equal(requests.length, 2);
    assert.match(requests[0][0].content, /D9-HEADER-ROLE/);
    assert.ok(requests[0].some(message => message.role === 'user' && String(message.content).includes('D9-BASELINE-PROFILE')));
    assert.equal(requests[1][0].role, 'system');
    assert.equal(requests[1][0].content, requests[0][0].content);
    const rows = await rowsOf(ctx, 'thread/actor/a');
    assert.equal(rows.filter(row => row.role === 'system' && !row.id.includes('/event/')).length, 1);
    const secondInput = rows.find(row => row.id === 'd9-actor-two/input');
    assert.equal(secondInput.value.content.includes('D9-BASELINE-PROFILE'), false);
});
