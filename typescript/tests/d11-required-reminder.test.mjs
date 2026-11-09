import test from 'node:test';
import assert from 'node:assert/strict';
import { driverFixture, assistant } from './c1-support.mjs';

function registerPublish(ctx) {
    let calls = 0;
    ctx.actions.register({ name: 'demo.publish', effect: 'read', inputSchema: { type: 'object', properties: { content: { type: 'string' } }, required: ['content'] }, outputSchema: {}, available: async () => true, execute: async (input, context) => {
        calls++;
        return { operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: [] };
    } });
    return () => calls;
}

async function reminders(ctx) {
    const view = await ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
        const rows = await view.query({ sql: "SELECT payload FROM thread_messages WHERE role='user' ORDER BY seq", parameters: [] }, { maxRows: 100, maxBytes: 1048576 });
        return rows.map(row => JSON.parse(String(row.payload)).value).filter(message => message?.content?.kind === 'required_action_reminder');
    } finally {
        await view.close();
    }
}

test('D11 a text-only turn reminds once about required actions and continues to the tool call', async t => {
    const requests = [];
    let calls;
    const text = assistant([{ type: 'text', text: '我先想想。' }]);
    const call = assistant([{ type: 'toolCall', id: 'd11-act-1', name: 'act', arguments: { action: 'demo.publish', input: { content: 'after reminder' } } }], 'toolUse');
    const { run, ctx } = await driverFixture({
        messages: [text, call],
        driver: { requiredActions: [{ name: 'demo.publish', minimumCompleted: 1 }], maxRequests: 2 },
        register: c => { calls = registerPublish(c); },
        observe: (messages, options) => requests.push(messages)
    });
    t.after(() => run.close());
    const context = run.context('d11-reminder');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({ id: 'd11-reminder', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'publish' }, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.equal(result.evidence.requests, 2, JSON.stringify(result.evidence));
    assert.equal(calls(), 1);
    assert.equal(requests.length, 2, JSON.stringify(requests));
    assert.equal(requests[0].some(message => typeof message.content === 'string' && message.content.includes('必需动作尚未完成')), false);
    assert.ok(requests[1].some(message => typeof message.content === 'string' && message.content.includes('必需动作尚未完成') && message.content.includes('demo.publish')), JSON.stringify(requests[1]));
    const stored = await reminders(ctx);
    assert.equal(stored.length, 1, JSON.stringify(stored));
    assert.equal(stored[0].content.kind, 'required_action_reminder', JSON.stringify(stored[0]));
    assert.equal(stored[0].content.source, 'agent-runtime-pi', JSON.stringify(stored[0]));
    assert.ok(stored[0].content.content.includes('demo.publish'), JSON.stringify(stored[0]));
});

test('D11 the reminder is sent at most once and then still fails when the action never runs', async t => {
    const { run, ctx } = await driverFixture({
        messages: [assistant([{ type: 'text', text: '还是只想想。' }]), assistant([{ type: 'text', text: '继续想想。' }]), assistant([{ type: 'text', text: '不做。' }])],
        driver: { requiredActions: [{ name: 'demo.publish', minimumCompleted: 1 }], maxRequests: 4 },
        register: registerPublish
    });
    t.after(() => run.close());
    const context = run.context('d11-reminder-once');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({ id: 'd11-reminder-once', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'publish' }, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'incomplete', JSON.stringify(result));
    assert.equal(result.value.reason, 'required_action_failed', JSON.stringify(result));
    assert.equal(result.evidence.requests, 2, JSON.stringify(result.evidence));
    assert.equal((await reminders(ctx)).length, 1);
});
