import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, threadRows, eventCommits, assistant} from './c1-support.mjs';

test('C1a persists a 300-delta turn in at most turns+acts+2 inference transactions', async t => {
    const {run, ctx} = await driverFixture({text: 'x'.repeat(300)});
    t.after(() => run.close());
    const context = run.context('c1-batching');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({id: 'c1-batching', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'stream 300 deltas'}, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.ok(eventCommits(result) <= 3, 'event transactions=' + eventCommits(result) + ' operations=' + JSON.stringify(result.requiredOperations));
    const rows = await threadRows(ctx);
    assert.equal(rows.filter(row => row.role === 'assistant').length, 1, JSON.stringify(rows));
    t.diagnostic(JSON.stringify({eventCommits: eventCommits(result), rows: rows.map(row => row.role)}));
});

test('C1a persists the assistant toolCall before the tool executes', async t => {
    let seenDuringTool = false;
    const call = assistant([{type: 'toolCall', id: 'boundary-act-1', name: 'act', arguments: {action: 'boundary.check', input: {}}}], 'toolUse');
    const done = assistant([{type: 'text', text: 'boundary done'}]);
    const {run, ctx} = await driverFixture({
        messages: [call, done],
        register(c) {
            c.actions.register({
                name: 'boundary.check',
                effect: 'read',
                inputSchema: {type: 'object', properties: {}, additionalProperties: false},
                outputSchema: {},
                available: async () => true,
                execute: async input => {
                    const view = await c.database.snapshot({maxAgeMs: 30000});
                    try {
                        const [thread] = await view.query({sql: 'SELECT id FROM threads WHERE actor_id=? AND moment_id=?', parameters: ['a', 'm']}, {maxRows: 1, maxBytes: 1024});
                        const rows = thread ? await view.query({sql: 'SELECT message_id,role,payload FROM thread_messages WHERE thread_id=?', parameters: [String(thread.id)]}, {maxRows: 10000, maxBytes: 1048576}) : [];
                        seenDuringTool = rows.some(row => String(row.role) === 'assistant' && String(row.payload).includes('boundary-act-1'));
                    } finally {
                        await view.close();
                    }
                    return {operationId: 'boundary-tool', status: 'completed', value: {seen: seenDuringTool}, original: null, artifacts: []};
                }
            });
        }
    });
    t.after(() => run.close());
    const context = run.context('c1-tool-boundary');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    let result;
    try {
        result = await driver.value.activate({id: 'c1-tool-boundary', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'call the boundary tool'}, context);
    } finally {
        await driver.release();
    }
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.equal(seenDuringTool, true, 'assistant toolCall was not visible before tool execution');
    const rows = await threadRows(ctx);
    const assistantRows = rows.filter(row => row.role === 'assistant');
    assert.equal(assistantRows.length, 2, JSON.stringify(rows));
    t.diagnostic(JSON.stringify({seenDuringTool, toolCallCommits: eventCommits(result)}));
});
