import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';

const source = () => ({
    id: 'd9/thread-perception',
    schema: {},
    cursorScope: 'thread',
    available: async () => true,
    versions: async () => [{owner: 'd9/thread-perception', version: '1'}],
    collect: async (scope, before) => {
        if (before === null || before === undefined) return {
            items: [{id: 'd9-thread-percept-1', kind: 'notice', material: {kind: 'inline', value: {text: 'first percept', thread: scope.threadId, moment: scope.momentId}}, provenance: {source: 'd9/thread-perception'}}],
            nextCursor: 1, through: 1, hasMore: false,
        };
        if (before === 1) return {
            items: [{id: 'd9-thread-percept-2', kind: 'notice', material: {kind: 'inline', value: {text: 'second percept', thread: scope.threadId, moment: scope.momentId}}, provenance: {source: 'd9/thread-perception'}}],
            nextCursor: 2, through: 2, hasMore: false,
        };
        return {items: [], nextCursor: before, through: before, hasMore: false};
    },
});

async function activateAt(ctx, run, id, moment) {
    const context = run.context(id, 'a', moment);
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        return await driver.value.activate({id, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: id}, context);
    } finally {
        await driver.release();
    }
}

async function rows(ctx) {
    const view = await ctx.database.snapshot({maxAgeMs: 30000});
    try {
        const [thread] = await view.query({sql: 'SELECT id FROM threads WHERE actor_id=? AND moment_id=?', parameters: ['a', 'thread/actor/a']}, {maxRows: 1, maxBytes: 1024});
        if (!thread) return {thread: undefined, rows: []};
        const rows = await view.query({sql: 'SELECT message_id,role,payload FROM thread_messages WHERE thread_id=? ORDER BY seq', parameters: [String(thread.id)]}, {maxRows: 10000, maxBytes: 1048576});
        return {thread: String(thread.id), rows: rows.map(row => {
            const stored = JSON.parse(String(row.payload));
            const message = stored.kind === 'inline' ? stored.value : null;
            const native = message && message.content && typeof message.content === 'object' && !Array.isArray(message.content) && typeof message.content.role === 'string' ? message.content : message;
            return {id: String(row.message_id), role: String(row.role), value: native};
        })};
    } finally {
        await view.close();
    }
}

test('D9 actor thread lifetime shares one thread across moments while perceptions append', async t => {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'one'}]), assistant([{type: 'text', text: 'two'}])],
        prompts: {sections: [{id: 'role', order: 0, text: 'D9-ACTOR-HEADER'}, {id: 'profile', order: 1, text: 'D9-ACTOR-BASELINE', scope: 'baseline'}]},
        driver: {threadLifetime: 'actor', perceptions: ['d9/thread-perception']},
        register(c) { c.perceptions.register(source()); },
        observe: messages => requests.push(messages),
    });
    t.after(() => run.close());

    const first = await activateAt(ctx, run, 'd9-actor-percept-one', 'm1');
    assert.equal(first.status, 'completed', JSON.stringify(first));
    const second = await activateAt(ctx, run, 'd9-actor-percept-two', 'm2');
    assert.equal(second.status, 'completed', JSON.stringify(second));

    const {thread, rows: messages} = await rows(ctx);
    assert.ok(thread.includes('thread/actor/a'), thread);
    assert.equal(messages.filter(message => message.role === 'system' && !message.id.includes('/event/')).length, 1);
    assert.ok(messages.some(message => message.id === 'd9-actor-percept-one/perception/0/d9/thread-perception'));
    assert.ok(messages.some(message => message.id === 'd9-actor-percept-two/perception/0/d9/thread-perception'));
    assert.equal(messages.find(message => message.id === 'd9-actor-percept-two/input').value.content.includes('D9-ACTOR-BASELINE'), false);
    assert.equal(requests.length, 2);
    assert.match(requests[0][0].content, /D9-ACTOR-HEADER/);
    assert.ok(requests[1].some(message => message.role === 'user' && String(message.content).includes('second percept')), JSON.stringify(requests[1]));
});
