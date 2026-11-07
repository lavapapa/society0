import { test } from 'node:test';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { setImmediate } from 'node:timers/promises';
import { fixture } from './metrics-support.mjs';
import { memoryWrite } from '../.build/extras/dist/memory/policies.js';

// C8 返工 1：真实 memory.extract 路径在 deferredWork 下只在 tick 内登记，不执行 5s 抽取。
test('C8 memory: deferred 抽取不进入 tick 墙钟，召回在 lag 内不阻塞、超过后阻塞', async () => {
  let startJob;
  const started = new Promise(resolve => { startJob = resolve; });
  const f = await fixture({
    ticks: 2,
    memory: true,
    deferred: true,
    deferredMaxLag: 1,
    onActivate: async (intent, context, ctx) => {
      if (Number(intent.input?.tick) !== 0) return;
      const thread = await f.read(view => ctx.threads.open(intent.actorId, intent.momentId, view));
      await context.database.transaction(context.writeLease, {
        operationId: 'c8-memory-seed', maxDurationMs: 20000, maxStatements: 16,
        maxInputBytes: 65536, maxChangedRows: 16, maxCapturedBytes: 262144
      }, tx => ctx.threads.append(tx, thread.id, {
        id: 'c8-experience', interactionId: thread.id, role: 'experience',
        content: { lesson: 'slow extraction' }, originalProviderFields: null
      }));
      const current = await f.read(view => ctx.threads.open(intent.actorId, intent.momentId, view));
      const values = await ctx.memory.extract(current, context);
      assert.deepEqual(values, [], 'deferred 路径不应同步返回抽取结果');
    }
  });
  try {
    await setImmediate();
    const selection = await f.ctx.slots.acquire(memoryWrite, { generation: 'read' });
    const policy = selection.items.find(item => item.id === 'explicit-experience/v1');
    assert.ok(policy, 'memory-write-experience policy 应已装配');
    const original = policy.value.extract.bind(policy.value);
    policy.value.extract = async (...args) => {
      startJob();
      await new Promise(resolve => setTimeout(resolve, 5000));
      return original(...args);
    };
    await selection.release();

    const began = performance.now();
    const result = await f.run();
    const tickWall = performance.now() - began;
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.ok(tickWall < 2000, `tick 墙钟包含了 5s 抽取: ${tickWall}ms`);

    await started;

    // job 登记在 tick 1。t=2 时消费方问 t-1=1：lag 1 未超上限，不阻塞。
    const within = performance.now();
    await f.ctx.memory.awaitRecall('2');
    const early = await f.read(view => f.ctx.memory.recall({ actorId: 'a', maxItems: 10, maxResponseBytes: 16384 }, view));
    assert.ok(performance.now() - within < 500, 'lag 内召回不应等待 5s');
    assert.equal(early.items.length, 0);

    // t=3 时消费方问 t-1=2：任务 tick 1 < 2，超出 lag，必须等到产出落盘。
    const beyond = performance.now();
    await f.ctx.memory.awaitRecall('3');
    assert.ok(performance.now() - beyond >= 3500, '超出 lag 的召回应等待后台抽取完成');
    const late = await f.read(view => f.ctx.memory.recall({ actorId: 'a', maxItems: 10, maxResponseBytes: 16384 }, view));
    assert.equal(late.items.length, 1);

    const view = await f.ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
      const rows = await view.query({ sql: 'SELECT produced_at,state FROM deferred_jobs' }, { maxRows: 10, maxBytes: 4096 });
      assert.equal(rows.length, 1);
      assert.equal(rows[0].state, 'done');
      assert.equal(String(rows[0].produced_at), '1', 'producedAt 应是登记的完整点 tick，而不是墙钟');
    } finally { await view.close(); }
  } finally { await f.host.close(); }
});
