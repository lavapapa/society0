import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture } from './metrics-support.mjs';

// C8：job 未完成时中断，恢复后按内容哈希幂等地只完成一次（DESIGN §5.2）。
const NAMESPACE = 'resume-extract';
const outputMigration = {
  owner: 'c8-resume-test', from: 0, to: 1, after: [], source: { consumer: 'c8-resume-test' },
  statements: [{ sql: 'CREATE TABLE c8_resume_out(id TEXT PRIMARY KEY NOT NULL,produced_at TEXT NOT NULL);' }]
};

function handler(marks) {
  return {
    async run(job, context) {
      const prior = await readFile(marks, 'utf8').catch(() => '');
      await appendFile(marks, 'start\n');
      // 第一次故意拖长以便中途 kill；恢复后的这次快速完成。
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, prior.includes('start') ? 20 : 10000);
        context.signal.addEventListener('abort', () => {
          clearTimeout(timer);
          reject(context.signal.reason);
        }, { once: true });
      });
      await context.transaction('c8-resume-' + job.id, async tx => {
        await tx.execute({ sql: 'INSERT INTO c8_resume_out VALUES(?,?)', parameters: [job.id, context.producedAt] });
      });
      await appendFile(marks, 'done\n');
      return { ok: true };
    }
  };
}

test('C8 resume: job 未完成时 kill，恢复后只完成一次', async () => {
  let startJob;
  const started = new Promise(resolve => { startJob = resolve; });
  const first = await fixture({
    ticks: 1,
    runId: 'c8-resume',
    deferred: true,
    migrations: [outputMigration],
    onActivate: (intent, _context, ctx) => {
      if (Number(intent.input?.tick) !== 0) return;
      ctx.deferredWork.enqueue({
        kind: 'resume-extract',
        inputs: { tick: intent.input.tick },
        produces: NAMESPACE,
        maxLagTicks: 1
      });
    }
  });
  const marks = join(first.directory, 'c8-resume-marks.txt');
  first.ctx.deferredWork.register('resume-extract', {
    async run(job, context) {
      startJob(job);
      return handler(marks).run(job, context);
    }
  });
  try {
    const result = await first.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));
    await started;
  } finally {
    // 模拟进程在 job 完成前退出：关闭宿主会取消后台任务。
    await first.host.close();
  }

  const before = await readFile(marks, 'utf8');
  assert.equal(before, 'start\n');

  const second = await fixture({
    directory: first.directory,
    ticks: 1,
    runId: 'c8-resume',
    deferred: true,
    migrations: [outputMigration]
  });
  try {
    await second.ctx.history.activate();
    second.ctx.deferredWork.register('resume-extract', handler(marks));
    await second.deferred.await(NAMESPACE, '2');
    const view = await second.ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
      const rows = await view.query({ sql: 'SELECT id,produced_at FROM c8_resume_out' }, { maxRows: 10, maxBytes: 4096 });
      assert.equal(rows.length, 1, '恢复后只应有一份产出');
      assert.ok(String(rows[0].produced_at).length > 0);
    } finally { await view.close(); }
    const after = await readFile(marks, 'utf8');
    assert.equal(after, 'start\nstart\ndone\n', '第二次启动只应完成一次');
  } finally { await second.host.close(); }
});
