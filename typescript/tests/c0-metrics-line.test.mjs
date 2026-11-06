// C0：每个 tick 结束写一行 metrics.jsonl，字段齐全且单行不超过 4KiB。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, curveMigration } from './metrics-support.mjs';

test('C0 three ticks write exactly three bounded metrics lines with the fixed fields', async () => {
  const f = await fixture({ actors: ['a', 'b'], ticks: 3, work: 'constant', migrations: [curveMigration] });
  try {
    const result = await f.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const text = await readFile(join(f.directory, 'metrics.jsonl'), 'utf8');
    const lines = text.trimEnd().split('\n');
    assert.equal(lines.length, 3);
    for (const [index, line] of lines.entries()) {
      assert.ok(Buffer.byteLength(line) <= 4096, `line ${index + 1} is ${Buffer.byteLength(line)} bytes`);
      const row = JSON.parse(line);
      assert.equal(row.tick, index + 1);
      assert.equal(typeof row.wallMs, 'number');
      assert.equal(typeof row.cpuMs, 'number');
      assert.equal(typeof row.rssMb, 'number');
      assert.equal(row.activations.total, 2, 'two actors per tick');
      assert.equal(row.activations.bySource.agenda, 2);
      assert.equal(typeof row.activations.notified, 'number');
      assert.equal(typeof row.activations.wakeDropped, 'number');
      assert.equal(typeof row.llm.requests, 'number');
      assert.equal(typeof row.tools.calls, 'number');
      assert.equal(typeof row.sqlite.rowsRead, 'number');
      assert.equal(typeof row.sqlRowsRead, 'number');
      assert.ok(row.sqlRowsRead > 0, 'the activation transactions read SQL rows');
      assert.equal(typeof row.history, 'object');
      assert.equal(typeof row.deferred, 'object');
    }
  }
  finally {
    await f.host.close();
  }
});

test('C0 inference observer counts requests, usage and tool calls without driver edits', async () => {
  const f = await fixture({ actors: ['a'], ticks: 2, work: 'constant', migrations: [curveMigration] });
  try {
    const slots = await import('../.build/core/dist/contracts/inference.js');
    const lease = await f.ctx.slots.acquire(slots.INFERENCE_OBSERVER, { generation: 'test' });
    try {
      const context = { actorId: 'a', momentId: 'm', logicalTime: '1', operationId: 'observer-test', signal: new AbortController().signal };
      for (const observer of lease.items) {
        await observer.value.observe({ type: 'message_start', message: { role: 'assistant', content: [], timestamp: 0 } }, context);
        await observer.value.observe({ type: 'message_end', message: {
          role: 'assistant', content: [{ type: 'text', text: 'ok' }], stopReason: 'stop', timestamp: 0,
          usage: { input: 120, output: 30, cacheRead: 20, cacheWrite: 0, totalTokens: 170, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }
        } }, context);
        await observer.value.observe({ type: 'tool_execution_start', toolCallId: 't1', toolName: 'read', args: {} }, context);
        await observer.value.observe({ type: 'tool_execution_end', toolCallId: 't1', toolName: 'read', result: { text: '0123456789' }, isError: false, durationMs: 4 }, context);
      }
    }
    finally { await lease.release(); }
    const result = await f.run();
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const rows = (await readFile(join(f.directory, 'metrics.jsonl'), 'utf8')).trimEnd().split('\n').map(line => JSON.parse(line));
    const first = rows[0];
    assert.equal(first.llm.requests, 1);
    assert.equal(first.llm.promptTokens, 120);
    assert.equal(first.llm.outTok, 30);
    assert.equal(first.llm.cacheReadTokens, 20);
    assert.equal(first.tools.calls, 1);
    assert.equal(first.tools.byName.read, 1);
    assert.equal(rows[1].llm.requests, 0, 'tick counters reset after each line');
  }
  finally {
    await f.host.close();
  }
});
