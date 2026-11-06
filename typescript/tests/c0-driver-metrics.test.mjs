// C0 driver 侧上报点：失败请求、请求延迟与模型准备等待时间。
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {driverFixture, assistant} from './c1-support.mjs';

async function activate(ctx, run, id) {
    const context = run.context(id);
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        return await driver.value.activate({id, actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'metrics'}, context);
    }
    finally {
        await driver.release();
    }
}

test('C0 driver reports failed requests, request latency and model wait time', async t => {
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'provider failed'}], 'error')],
        metrics: true
    });
    t.after(() => run.close());
    const result = await activate(ctx, run, 'c0-metrics-failed');
    assert.equal(result.status, 'incomplete', JSON.stringify(result));
    assert.equal(result.value.reason, 'provider_error', JSON.stringify(result));
    const snapshot = ctx.get('metrics').snapshot();
    assert.equal(snapshot.counters['llm.failedRequests'], 1);
    assert.equal(snapshot.observations['llm.requestLatencyMs']?.count, 1);
    assert.equal(snapshot.observations['llm.queueWaitMs']?.count, 1);
    await ctx.get('metrics').tick({sequence: '1'});
    const row = JSON.parse((await readFile(join(run.directory, 'metrics.jsonl'), 'utf8')).trimEnd());
    assert.equal(row.llm.requests, 1, 'observer already counts the message_end request');
    assert.equal(row.llm.failedRequests, 1);
    assert.equal(typeof row.llm.latencyP95Ms, 'number');
    assert.equal(typeof row.llm.queueWaitP95Ms, 'number');
});

test('C0 driver keeps a successful request out of the failed counter', async t => {
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'ok'}])],
        metrics: true
    });
    t.after(() => run.close());
    const result = await activate(ctx, run, 'c0-metrics-success');
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const snapshot = ctx.get('metrics').snapshot();
    assert.equal(snapshot.counters['llm.failedRequests'] ?? 0, 0);
    assert.equal(snapshot.observations['llm.requestLatencyMs']?.count, 1);
    assert.equal(snapshot.observations['llm.queueWaitMs']?.count, 1);
});
