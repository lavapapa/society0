import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, assistant} from './c1-support.mjs';
import {estimateSlots} from './live/support.mjs';
import {contextBudget} from '../.build/core/dist/contracts/context.js';

const calls = {
    read: {name: 'read', arguments: {path: '/workspace'}},
    find: {name: 'find', arguments: {root: '/workspace'}},
    grep: {name: 'grep', arguments: {pattern: 'x', root: '/workspace'}},
};

test('L1 slot estimator accepts assistant wire messages with null content', () => {
    const record = {
        toolSchemaBytes: 2000,
        messages: [
            {role: 'system', content: 'system prompt'},
            {role: 'user', content: '本次输入：{"probe":"x"}'},
            {role: 'assistant', content: null, toolCalls: [{id: 'c1', function: {name: 'read', arguments: '{"path":"/workspace"}'}}]},
            {role: 'tool', content: 'tool output', tool_call_id: 'c1'},
        ],
        response: {usage: {prompt_tokens: 100, completion_tokens: 10}, content: 'done'},
    };
    const slots = estimateSlots(record, contextBudget(), 'BASELINE');
    assert.equal(typeof slots.header, 'number');
    assert.equal(slots.output, 10);
    assert.ok(slots.total > 0);
});

for (const [tool, call] of Object.entries(calls)) {
    test(`L1 ${tool} tool result is followed by a second request without MODEL_TRANSPORT_UNKNOWN`, async t => {
        const requests = [];
        const {run, ctx} = await driverFixture({
            messages: [assistant([{type: 'toolCall', id: 'l1-' + tool, name: tool, arguments: call.arguments}], 'toolUse'), assistant([{type: 'text', text: 'done'}])],
            driver: {maxRequests: 4, maxToolCalls: 4},
            observe: messages => requests.push(messages),
        });
        t.after(() => run.close());
        const context = run.context('l1-followup-' + tool);
        const driver = await ctx.drivers.acquire('llm/c1', context);
        let result;
        try {
            result = await driver.value.activate({id: 'l1-followup-' + tool, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: null}, context);
        } finally {
            await driver.release();
        }
        assert.notEqual(result.reason, 'transport_unknown');
        assert.equal(requests.length, 2, JSON.stringify(requests));
        assert.ok(requests[1].some(message => message.role === 'toolResult'), JSON.stringify(requests[1]));
    });
}
