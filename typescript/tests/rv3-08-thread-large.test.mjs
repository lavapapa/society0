import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {readdir, stat} from 'node:fs/promises';
import {Threads} from '../.build/core/dist/thread/index.js';
import {setup, read} from './pr07-support.mjs';

const codeOf = error => error && typeof error === 'object' ? error.code : undefined;

async function countRefs(directory) {
  const objects = join(directory, 'objects');
  let count = 0;
  for (const entry of await readdir(objects, {withFileTypes: true})) {
    if (!entry.isDirectory()) continue;
    try {
      await stat(join(objects, entry.name, 'ref.json'));
      count++;
    } catch {}
  }
  return count;
}

test('RV3-08-T02 eight MiB message keeps live memory bounded and exact bytes', async () => {
  const child = fork(fileURLToPath(new URL('./rv3-08-thread-child.mjs', import.meta.url)), {
    execArgv: ['--expose-gc'],
    stdio: ['ignore', 'pipe', 'inherit', 'ipc'],
  });
  let stdout = '';
  child.stdout.on('data', chunk => { stdout += chunk; });
  const result = await new Promise(resolve => child.once('exit', (code, signal) => resolve({code, signal})));
  assert.equal(result.code, 0, stdout);
  const line = stdout.trim().split('\n').filter(Boolean).at(-1);
  const proof = JSON.parse(line).rv308Proof;
  assert.ok(proof.utf8Bytes > 8_000_000);
  assert.ok(proof.maxChunk <= 65536);
  assert.ok(proof.arrayBuffersLiveDelta <= 1 << 20, JSON.stringify(proof));
  assert.ok(proof.heapUsedLiveDelta <= proof.strBytes + (4 << 20), JSON.stringify(proof));
  assert.ok(proof.finalArrayBuffersDelta <= 1 << 20, JSON.stringify(proof));
  assert.ok(proof.finalHeapUsedDelta <= 1 << 20, JSON.stringify(proof));
});

test('RV3-08-T03 over maxMessageBytes and non-JSON reject before stringify', async () => {
  const env = await setup({threadPlugin: Threads});
  const originalStringify = JSON.stringify;
  const originalStage = env.ctx.artifacts.stage.bind(env.ctx.artifacts);
  const message = {id: 'rv308-over', interactionId: 'rv308-over-interaction', role: 'user', content: 'x'.repeat(17 * 1024 * 1024), originalProviderFields: null};
  const big = {id: 'rv308-bigint', interactionId: 'rv308-bigint-interaction', role: 'user', content: 1n, originalProviderFields: null};
  let stringifiedMessage = 0;
  let stringifiedBig = 0;
  let staged = 0;
  try {
    JSON.stringify = function (value, ...rest) {
      if (value === message) stringifiedMessage++;
      if (value === big) stringifiedBig++;
      return originalStringify.call(this, value, ...rest);
    };
    env.ctx.artifacts.stage = (...args) => { staged++; return originalStage(...args); };
    await assert.rejects(env.ctx.threads.prepare(message), error => codeOf(error) === 'THREAD_MESSAGE_BYTES');
    assert.equal(stringifiedMessage, 0);
    assert.equal(staged, 0);
    await assert.rejects(env.ctx.threads.prepare(big), error => codeOf(error) === 'THREAD_MESSAGE_INVALID');
    assert.equal(stringifiedBig, 0);
    assert.equal(staged, 0);
  } finally {
    JSON.stringify = originalStringify;
    env.ctx.artifacts.stage = originalStage;
    await env.close();
  }
});

test('RV3-08-T04 cancellation happens in the stage phase', async () => {
  const env = await setup({threadPlugin: Threads});
  const originalStage = env.ctx.artifacts.stage.bind(env.ctx.artifacts);
  const controller = new AbortController();
  let pulled = 0;
  try {
    const before = await read(env.ctx, view => env.ctx.threads.open('a', 'm', view));
    const message = {id: 'rv308-cancel', interactionId: 'rv308-cancel-interaction', role: 'user', content: 'x'.repeat(2 * 1024 * 1024), originalProviderFields: null};
    env.ctx.artifacts.stage = async (source, mediaType, options) => {
      const measured = (async function* () {
        for await (const chunk of source) {
          pulled++;
          yield chunk;
          if (pulled === 2) controller.abort(Error('cancel stage'));
        }
      })();
      return originalStage(measured, mediaType, options);
    };
    await assert.rejects(env.ctx.threads.prepare(message, controller.signal), /cancel stage|Abort/);
    assert.ok(pulled <= 3);
    assert.equal(env.ctx.artifacts.diagnostics().active, 0);
    assert.equal(await countRefs(env.ctx.artifacts.directory), 0);
    const after = await read(env.ctx, view => env.ctx.threads.open('a', 'm', view));
    assert.equal(after.headSeq, before.headSeq);
  } finally {
    env.ctx.artifacts.stage = originalStage;
    await env.close();
  }
});
