import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';

function registerPublish(ctx) {
    let calls = 0;
    ctx.actions.register({name: 'demo.publish', effect: 'read', inputSchema: {type: 'object', properties: {content: {type: 'string'}}, required: ['content']}, outputSchema: {}, available: async () => true, execute: async (input, context) => {
        calls++;
        return {operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: []};
    }});
    return () => calls;
}

test('D7 textual tool call is corrected once and then the real act executes', async t => {
    const requests = [];
    let calls;
    const text = assistant([{type: 'text', text: '{"action":"demo.publish","input":{"content":"written as text"}}'}]);
    const call = assistant([{type: 'toolCall', id: 'd7-act-1', name: 'act', arguments: {action: 'demo.publish', input: {content: 'real call'}}}], 'toolUse');
    const done = assistant([{type: 'text', text: 'done'}]);
    const {run, ctx} = await driverFixture({
        messages: [text, call, done],
        driver: {requiredActions: [{name: 'demo.publish', minimumCompleted: 1}], textualToolCallRetries: 1},
        register: c => { calls = registerPublish(c); },
        observe: (messages, options) => requests.push({messages, options})
    });
    t.after(() => run.close());
    const context = run.context('d7-textual');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({id: 'd7-textual', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'publish'}, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.equal(result.evidence.textualToolCall, 1, JSON.stringify(result.evidence));
    assert.equal(calls(), 1);
    assert.equal(requests.length, 3);
    assert.ok(requests[1].messages.some(message => typeof message.content === 'string' && message.content.includes('你把行动写成了正文')), JSON.stringify(requests[1].messages));
    assert.equal(requests[0].messages.some(message => typeof message.content === 'string' && message.content.includes('你把行动写成了正文')), false);
});

test('D7 retries=0 leaves a textual-only turn as required_action_failed', async t => {
    let calls;
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: '{"action":"demo.publish","input":{"content":"not executed"}}'}]), assistant([{type: 'text', text: 'done'}])],
        driver: {requiredActions: [{name: 'demo.publish', minimumCompleted: 1}], textualToolCallRetries: 0},
        register: c => { calls = registerPublish(c); }
    });
    t.after(() => run.close());
    const context = run.context('d7-no-retry');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({id: 'd7-no-retry', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'publish'}, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'incomplete', JSON.stringify(result));
    assert.equal(result.value.reason, 'required_action_failed', JSON.stringify(result));
    assert.equal(calls(), 0);
});

test('D7 required-first only sets tool_choice on the first request', async t => {
    const options = [];
    let calls;
    const call = assistant([{type: 'toolCall', id: 'd7-choice-1', name: 'act', arguments: {action: 'demo.publish', input: {content: 'choice'}}}], 'toolUse');
    const {run, ctx} = await driverFixture({
        messages: [call, assistant([{type: 'text', text: 'done'}])],
        driver: {requiredActions: [{name: 'demo.publish', minimumCompleted: 1}], toolChoice: 'required-first'},
        register: c => { calls = registerPublish(c); },
        observe: (_messages, value) => options.push(value)
    });
    t.after(() => run.close());
    const context = run.context('d7-choice');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({id: 'd7-choice', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'publish'}, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.equal(calls(), 1);
    assert.equal(options[0].toolChoice, 'required');
    assert.equal(options[1].toolChoice, undefined);
});
