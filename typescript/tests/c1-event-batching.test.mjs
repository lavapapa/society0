import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, threadRows, eventCommits} from './c1-support.mjs';

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
