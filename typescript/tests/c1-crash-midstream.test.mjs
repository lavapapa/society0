import test from 'node:test';
import assert from 'node:assert/strict';
import {driverFixture, threadRows} from './c1-support.mjs';

test('C1a crash mid-stream leaves no half assistant message and the retry is complete', async t => {
    const {run, ctx} = await driverFixture({text: 'recovered', crashAfter: 2});
    t.after(() => run.close());
    const first = run.context('c1-crash-one');
    const driver = await ctx.drivers.acquire('llm/c1', first);
    let crashed;
    try {
        crashed = await driver.value.activate({id: 'c1-crash-one', actorId: first.actorId, momentId: first.momentId, source: 'test', driver: 'llm/c1', input: 'crash mid stream'}, first);
    } finally {
        await driver.release();
    }
    assert.equal(crashed.status, 'incomplete', JSON.stringify(crashed));
    const afterCrash = await threadRows(ctx);
    const firstActivation = afterCrash.filter(row => row.message_id.startsWith('c1-crash-one/event/'));
    assert.equal(firstActivation.length, 0, 'partial stream persisted=' + JSON.stringify(afterCrash));
    assert.equal(afterCrash.filter(row => row.role === 'assistant').length, 0, 'partial assistant persisted=' + JSON.stringify(afterCrash));

    const second = run.context('c1-crash-two');
    const retry = await ctx.drivers.acquire('llm/c1', second);
    let completed;
    try {
        completed = await retry.value.activate({id: 'c1-crash-two', actorId: second.actorId, momentId: second.momentId, source: 'test', driver: 'llm/c1', input: 'retry after crash'}, second);
    } finally {
        await retry.release();
    }
    assert.equal(completed.status, 'completed', JSON.stringify(completed));
    const rows = await threadRows(ctx);
    assert.equal(rows.filter(row => row.role === 'assistant').length, 1, JSON.stringify(rows));
    assert.equal(rows.filter(row => row.message_id.startsWith('c1-crash-one/event/')).length, 0, JSON.stringify(rows));
    t.diagnostic(JSON.stringify({rows: rows.map(row => row.role)}));
});
