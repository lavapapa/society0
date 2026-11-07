import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture } from './metrics-support.mjs';

// C8 返工：失败的 job 不自动重试，但必须出现在 diagnostics 与 metrics。
test('C8 failed job: diagnostics 与 metrics 都能看到 deferred.failed', async () => {
  const f = await fixture({
    ticks: 1,
    deferred: true,
    onActivate: (intent, _context, ctx) => {
      if (Number(intent.input?.tick) !== 0) return;
      ctx.deferredWork.enqueue({ kind: 'fail-once', inputs: {}, produces: 'fail-ns', maxLagTicks: 1 });
    }
  });
  try {
    f.ctx.deferredWork.register('fail-once', { async run() { throw Error('expected deferred failure'); } });
    const result = await f.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));
    for (let attempt = 0; attempt < 200 && f.ctx.deferredWork.failures().length === 0; attempt++) await delay(10);
    const failures = f.ctx.deferredWork.failures();
    assert.equal(failures.length, 1);
    assert.match(failures[0].error, /expected deferred failure/);
    assert.equal(f.ctx.deferredWork.describe().failed, 1);

    await f.ctx.metrics.tick({ sequence: '99', logicalTime: '99' });
    const lines = (await readFile(join(f.directory, 'metrics.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    // 手动 tick 前的最后一行在失败后也可能已记录；任一真实行出现 failed=1 即可。
    assert.ok(lines.some(line => Number(line.deferred?.failed) === 1), 'metrics.jsonl 应记录 deferred.failed');
  } finally { await f.host.close(); }
});
