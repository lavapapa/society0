import { test } from 'node:test';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { createHost } from '../.build/host/dist/index.js';
import RunController from '../.build/core/dist/run-controller/index.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const busy = () => Error('FENCE_BUSY: executing');

async function controller(options = {}) {
  const host = await createHost({ boot: [] });
  const ctx = host.ctx;
  const state = { resumes: [], published: 0 };
  ctx.provide('history', {
    bindQuiescence() {},
    publish: async (_lease, request) => {
      state.published++;
      return { id: 'cut-next', revision: '9', parentId: request.parentId, frontier: { codec: 'staged/1', value: '0', logicalTime: '0', completedCount: '1', pendingAcknowledgements: [] } };
    },
  });
  ctx.provide('database', { readRevision: async () => '9' });
  ctx.provide('executionFence', {
    seal: options.seal ?? (async () => ({ kind: 'publication', id: 'seal', sealedRevision: '9' })),
    reserve: options.reserve ?? (async () => ({ lease: {}, signal: new AbortController().signal, cancel() {}, release: async () => {} })),
    resume(lease) { state.resumes.push(lease); },
    halt() {},
  });
  ctx.provide('generationViews', {});
  const runs = new RunController(ctx, options.config ?? { maxTransactionMs: 120 });
  return { host, ctx, runs, state };
}

const cut = () => ({ id: 'cut-current', revision: '8', generation: 'g', controlSequence: '0', frontier: { codec: 'staged/1', value: '0', logicalTime: '0', completedCount: '0', pendingAcknowledgements: [] } });
const view = () => ({ source: { acknowledge: async () => {} } });

test('publish stops promptly when the run signal aborts during seal retries', async () => {
  const { host, runs } = await controller({ seal: async () => { throw busy(); } });
  try {
    const abort = new AbortController();
    setTimeout(() => abort.abort(Error('run cancelled')), 40);
    const started = performance.now();
    await assert.rejects(runs.publish(cut(), [], view(), abort.signal), /cancel|abort/i);
    assert.ok(performance.now() - started < 500, 'abort must not wait for the retry deadline');
  } finally {
    await host.close();
  }
});

test('continuous FENCE_BUSY fails within the configured transaction budget', async () => {
  let calls = 0;
  const { host, runs } = await controller({ seal: async () => { calls++; throw busy(); }, config: { maxTransactionMs: 120 } });
  try {
    const started = performance.now();
    await assert.rejects(runs.publish(cut(), [], view(), new AbortController().signal), /FENCE_BUSY/);
    const elapsed = performance.now() - started;
    assert.ok(calls >= 2, 'retry must be attempted more than once');
    assert.ok(elapsed >= 100, `retry budget should be consumed before failing (${elapsed}ms)`);
    assert.ok(elapsed < 2000, `failure must stay inside the derived budget (${elapsed}ms)`);
  } finally {
    await host.close();
  }
});

test('a short FENCE_BUSY burst is retried until seal succeeds', async () => {
  let calls = 0;
  const { host, runs, state } = await controller({
    seal: async () => {
      calls++;
      if (calls < 3) throw busy();
      return { kind: 'publication', id: 'seal', sealedRevision: '9' };
    },
  });
  try {
    const next = await runs.publish(cut(), [], view(), new AbortController().signal);
    assert.equal(next.revision, '9');
    assert.equal(calls, 3);
    assert.equal(state.published, 1);
    assert.equal(state.resumes.length, 1);
  } finally {
    await host.close();
  }
});

test('reserve receives the run signal and retries are bounded', async () => {
  const seen = [];
  const { host, runs } = await controller({
    reserve: async (_proposal, signal) => {
      seen.push(signal);
      throw busy();
    },
    config: { maxTransactionMs: 80 },
  });
  try {
    const abort = new AbortController();
    await assert.rejects(runs.busy(signal => runs.ctx.executionFence.reserve({ id: 'p' }, signal), abort.signal), /FENCE_BUSY/);
    assert.ok(seen.length >= 1);
    assert.ok(seen.every(signal => signal === abort.signal), 'reserve must receive the run signal');
  } finally {
    await host.close();
  }
});
