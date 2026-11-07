import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate } from 'node:timers/promises';
import { fixture } from './metrics-support.mjs';
import DeferredWork from '../.build/core/dist/deferred-work/index.js';

// C8 返工 2：extras 先装配、deferredWork 后装配时仍要注册 handler 并执行。
test('C8 late assembly: deferredWork 在 extras 之后装配仍能执行抽取', async () => {
  const f = await fixture({
    ticks: 1,
    memory: true,
    deferred: false,
    deferredMigration: true,
    deferredMaxLag: 1,
    onActivate: async (intent, context, ctx) => {
      if (Number(intent.input?.tick) !== 0) return;
      const thread = await f.read(view => ctx.threads.open(intent.actorId, intent.momentId, view));
      await context.database.transaction(context.writeLease, {
        operationId: 'c8-late-seed', maxDurationMs: 20000, maxStatements: 16,
        maxInputBytes: 65536, maxChangedRows: 16, maxCapturedBytes: 262144
      }, tx => ctx.threads.append(tx, thread.id, {
        id: 'c8-late-experience', interactionId: thread.id, role: 'experience',
        content: { lesson: 'late assembly' }, originalProviderFields: null
      }));
      const current = await f.read(view => ctx.threads.open(intent.actorId, intent.momentId, view));
      assert.deepEqual(await ctx.memory.extract(current, context), []);
    }
  });
  try {
    assert.equal(f.deferred, undefined, 'fixture 不应提前安装 deferredWork');
    await setImmediate();
    await f.ctx.plugin(DeferredWork, {});
    const result = await f.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));

    let timer;
    await Promise.race([
      f.ctx.deferredWork.await('memory-write-experience', '2'),
      new Promise((_, reject) => { timer = setTimeout(() => reject(Error('late deferred handler not registered')), 5000); })
    ]).finally(() => clearTimeout(timer));

    const page = await f.read(view => f.ctx.memory.recall({ actorId: 'a', maxItems: 10, maxResponseBytes: 16384, logicalTime: '2' }, view));
    assert.equal(page.items.length, 1);
    assert.equal(page.items[0].body.lesson, 'late assembly');
  } finally { await f.host.close(); }
});
