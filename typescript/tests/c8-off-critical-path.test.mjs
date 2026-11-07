import { test } from 'node:test';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture } from './metrics-support.mjs';

// C8：deferred work 不在 tick 关键路径上（DESIGN §5.2）。
const NAMESPACE = 'fake-extract';
const outputMigration = {
  owner: 'c8-test', from: 0, to: 1, after: [], source: { consumer: 'c8-test' },
  statements: [{ sql: 'CREATE TABLE c8_out(id TEXT PRIMARY KEY NOT NULL,produced_at TEXT NOT NULL);' }]
};

test('C8 defer: 5s 抽取不进入 tick 墙钟，await 在 lag 内不阻塞、超出后阻塞', async () => {
  let startJob;
  const started = new Promise(resolve => { startJob = resolve; });
  const f = await fixture({
    ticks: 2,
    deferred: true,
    migrations: [outputMigration],
    onActivate: (intent, _context, ctx) => {
      if (Number(intent.input?.tick) !== 0) return;
      ctx.deferredWork.enqueue({
        kind: 'fake-extract',
        inputs: { tick: intent.input.tick },
        produces: NAMESPACE,
        maxLagTicks: 2
      });
    }
  });
  try {
    f.ctx.deferredWork.register('fake-extract', {
      async run(job, context) {
        startJob(job);
        await new Promise((resolve, reject) => {
          const timer = setTimeout(resolve, 5000);
          context.signal.addEventListener('abort', () => {
            clearTimeout(timer);
            reject(context.signal.reason);
          }, { once: true });
        });
        await context.transaction('c8-out-' + job.id, async tx => {
          await tx.execute({ sql: 'INSERT INTO c8_out VALUES(?,?)', parameters: [job.id, context.producedAt] });
        });
        return { ok: true };
      }
    });

    const began = performance.now();
    const result = await f.run();
    const tickWall = performance.now() - began;
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.ok(tickWall < 2000, `tick 推进包含了 deferred 工作: ${tickWall}ms`);

    await started;

    // 任务登记在 tick 1。t=2 时消费方问 t-1=1：lag 1 未超上限，不阻塞。
    const within = performance.now();
    await f.deferred.await(NAMESPACE, '1');
    assert.ok(performance.now() - within < 500, 'lag 内 await 不应等待 5s 任务');

    // t=3 时消费方问 t-1=2：任务 tick 1 < 2，lag 2 超出上限，必须阻塞到产出落盘。
    const beyond = performance.now();
    await f.deferred.await(NAMESPACE, '2');
    const waited = performance.now() - beyond;
    assert.ok(waited >= 3500, `超出 lag 的 await 应立即等待任务完成，实际 ${waited}ms`);

    const view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
      const rows = await view.query({ sql: 'SELECT id,produced_at FROM c8_out' }, { maxRows: 10, maxBytes: 4096 });
      assert.equal(rows.length, 1);
      assert.ok(String(rows[0].produced_at).length > 0);
    } finally { await view.close(); }

    const metrics = (await readFile(join(f.directory, 'metrics.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.ok(metrics.some(line => Number(line.deferred?.queued) >= 1), 'metrics.jsonl 的 deferred.queued 应来自真实队列');
    assert.ok(metrics.some(line => Number(line.deferred?.lagTicks) >= 0));
  } finally { await f.host.close(); }
});
