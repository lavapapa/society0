// C9：perf-curve 对每个计数器做线性/二次回归；O(T²) 扫描必须被定位到行读取计数。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, curveMigration } from './metrics-support.mjs';
import { analyzeMetrics } from '../bench/perf-curve.mjs';

test('C9 detector reports suspected O(T²) and names the row-read counter', async () => {
  const f = await fixture({ actors: ['a'], ticks: 50, work: 'quadratic', migrations: [curveMigration] });
  try {
    const result = await f.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const text = await readFile(join(f.directory, 'metrics.jsonl'), 'utf8');
    const report = analyzeMetrics(text, { minTicks: 20, maxSlopePerTick: 0.02 });
    assert.equal(report.status, 'fail');
    assert.equal(report.quadratic, true);
    assert.match(report.fastest, /rowsRead/);
    assert.ok(report.violations.some(item => /rowsRead/.test(item.name) && item.quadratic), JSON.stringify(report.violations));
  }
  finally {
    await f.host.close();
  }
});

test('C9 detector accepts a constant rule workload', async () => {
  const f = await fixture({ actors: ['a'], ticks: 50, work: 'constant', migrations: [curveMigration] });
  try {
    const result = await f.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const text = await readFile(join(f.directory, 'metrics.jsonl'), 'utf8');
    const report = analyzeMetrics(text, { minTicks: 20, maxSlopePerTick: 0.02 });
    assert.equal(report.quadratic, false);
    assert.equal(report.status, 'pass', JSON.stringify(report.violations));
  }
  finally {
    await f.host.close();
  }
});
