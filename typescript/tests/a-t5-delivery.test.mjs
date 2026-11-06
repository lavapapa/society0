import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';

const target = id => ({kind: 'demo/post', id});

function demoProvider(state) {
    state.valid = new Set(['t1', 't2']);
    state.calls = 0;
    state.mutateAfterFirst = state.mutateAfterFirst === true;
    return {
        id: 'demo',
        actions: ['demo.reply'],
        versions: async () => [],
        offers: async () => {
            state.calls++;
            const offers = [...state.valid].sort().map(id => ({action: 'demo.reply', target: target(id), prefill: {postId: id}, needs: ['content']}));
            if (state.mutateAfterFirst && state.calls === 1) state.valid = new Set(['t2']);
            return offers;
        }
    };
}

async function messagesOf(ctx, actor = 'a', moment = 'm') {
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

function registerDemo(ctx, state) {
    ctx.actions.register({name: 'demo.reply', description: '回复示例帖子', effect: 'read', targets: {kinds: ['demo/post'], required: true}, inputSchema: {type: 'object', properties: {postId: {type: 'string'}, content: {type: 'string'}}, required: ['postId', 'content'], additionalProperties: false}, outputSchema: {}, available: async () => true, execute: async (input, context) => ({operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: []})});
    ctx.actions.provide(demoProvider(state));
}

async function activate(ctx, run, id) {
    const context = run.context(id);
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        return await driver.value.activate({id, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: id}, context);
    } finally {
        await driver.release();
    }
}

test('A-T5 first delivery is full, unchanged delivery is omitted and expired offers are removed', async t => {
    const state = {};
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}]), assistant([{type: 'text', text: 'two'}]), assistant([{type: 'text', text: 'three'}])],
        register: c => registerDemo(c, state)
    });
    t.after(() => run.close());
    assert.equal((await activate(ctx, run, 'a-t5-one')).status, 'completed');
    const first = (await messagesOf(ctx)).filter(message => message.id === 'a-t5-one/input');
    assert.equal(first.length, 1);
    assert.match(first[0].value.content, /可用行动\nschema demo\.reply/);
    assert.match(first[0].value.content, /\+ demo\.reply demo\/post:t1/);
    assert.match(first[0].value.content, /\+ demo\.reply demo\/post:t2/);
    assert.match(first[0].value.content, /预填\{"postId":"t1"\}/);

    assert.equal((await activate(ctx, run, 'a-t5-two')).status, 'completed');
    const second = (await messagesOf(ctx)).filter(message => message.id === 'a-t5-two/input');
    assert.equal(second.length, 1);
    assert.equal(second[0].value.content.includes('可用行动'), false, second[0].value.content);

    state.valid = new Set(['t2']);
    assert.equal((await activate(ctx, run, 'a-t5-three')).status, 'completed');
    const third = (await messagesOf(ctx)).filter(message => message.id === 'a-t5-three/input');
    assert.equal(third.length, 1);
    assert.match(third[0].value.content, /可用行动（变更）/);
    assert.match(third[0].value.content, /- demo\.reply demo\/post:t1/);
    assert.equal(third[0].value.content.includes('schema demo.reply'), false, third[0].value.content);
    assert.equal(third[0].value.content.includes('demo/post:t2'), false, third[0].value.content);
});

test('A-T5 requests keep the previous messages as a prefix and a stable tool list', async t => {
    const state = {};
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}]), assistant([{type: 'text', text: 'two'}])],
        register: c => registerDemo(c, state),
        observe: messages => requests.push(messages)
    });
    t.after(() => run.close());
    assert.equal((await activate(ctx, run, 'a-t5-prefix-one')).status, 'completed');
    assert.equal((await activate(ctx, run, 'a-t5-prefix-two')).status, 'completed');
    assert.equal(requests.length, 2);
    assert.deepEqual(requests[1].slice(0, requests[0].length), requests[0]);
    t.diagnostic(JSON.stringify({first: requests[0].length, second: requests[1].length}));
});

test('A-T5 act on an expired offer returns ACTION_EXPIRED and appends the change segment', async t => {
    const state = {mutateAfterFirst: true};
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'toolCall', id: 'expired-act-1', name: 'act', arguments: {action: 'demo.reply', target: 'demo/post:t1', input: {postId: 't1', content: 'hello'}}}], 'toolUse'), assistant([{type: 'text', text: 'done'}])],
        register: c => registerDemo(c, state)
    });
    t.after(() => run.close());
    const result = await activate(ctx, run, 'a-t5-expired');
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const rows = await messagesOf(ctx);
    const tool = rows.find(row => row.role === 'toolResult');
    assert.ok(tool, JSON.stringify(rows));
    const text = tool.value.content[0].text;
    assert.match(text, /"code":"ACTION_EXPIRED"/);
    assert.match(text, /"alternatives":\[/);
    assert.match(text, /"target":"demo\/post:t2"/);
    assert.match(text, /可用行动（变更）\n- demo\.reply demo\/post:t1/);
    assert.equal(text.includes('schema demo.reply'), false, text);
});
