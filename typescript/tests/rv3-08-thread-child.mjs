import assert from 'node:assert/strict';
import {Threads} from '../.build/core/dist/thread/index.js';
import {setup} from './pr07-support.mjs';

const env = await setup({threadPlugin: Threads});
let exitCode = 0;
try {
  const message = {
    id: 'rv308-child',
    interactionId: 'rv308-child-interaction',
    role: 'user',
    content: ('中文abc').repeat(932068),
    originalProviderFields: null,
  };
  const utf8Bytes = Buffer.byteLength(JSON.stringify(message));
  const strBytes = 2 * JSON.stringify(message).length;
  globalThis.gc();
  const baseline = process.memoryUsage();
  const samples = [];
  const originalStage = env.ctx.artifacts.stage.bind(env.ctx.artifacts);
  env.ctx.artifacts.stage = async (source, mediaType, options) => {
    const measured = (async function* () {
      for await (const chunk of source) {
        globalThis.gc();
        const usage = process.memoryUsage();
        samples.push({bytes: chunk.byteLength, arrayBuffers: usage.arrayBuffers, heapUsed: usage.heapUsed});
        yield chunk;
      }
    })();
    return originalStage(measured, mediaType, options);
  };
  const started = Date.now();
  const prepared = await env.ctx.threads.prepare(message);
  const payload = prepared.payload;
  const ms = Date.now() - started;
  globalThis.gc();
  const final = process.memoryUsage();
  assert.equal(payload.kind, 'reference');
  assert.equal(payload.ref.rawBytes, String(utf8Bytes));
  assert.ok(samples.length > 0);
  assert.ok(Math.max(...samples.map(item => item.bytes)) <= env.ctx.artifacts.chunkBytes);
  const arrayBuffersLiveDelta = Math.max(0, ...samples.map(item => item.arrayBuffers - baseline.arrayBuffers));
  const heapUsedLiveDelta = Math.max(0, ...samples.map(item => item.heapUsed - baseline.heapUsed));
  const finalArrayBuffersDelta = Math.max(0, final.arrayBuffers - baseline.arrayBuffers);
  const finalHeapUsedDelta = Math.max(0, final.heapUsed - baseline.heapUsed);
  assert.ok(arrayBuffersLiveDelta <= 1 << 20, `arrayBuffers live delta ${arrayBuffersLiveDelta}`);
  assert.ok(heapUsedLiveDelta <= strBytes + (4 << 20), `heapUsed live delta ${heapUsedLiveDelta}`);
  assert.ok(finalArrayBuffersDelta <= 1 << 20, `final arrayBuffers ${finalArrayBuffersDelta}`);
  assert.ok(finalHeapUsedDelta <= 1 << 20, `final heapUsed ${finalHeapUsedDelta}`);
  const tail = [];
  for await (const part of env.ctx.threads.readPayload(payload.ref, {offset: String(utf8Bytes - 73), length: 73, maxBytes: 73})) tail.push(part);
  assert.deepEqual(Buffer.concat(tail), Buffer.from(JSON.stringify(message)).subarray(utf8Bytes - 73));
  console.log(JSON.stringify({rv308Proof: {utf8Bytes, strBytes, maxChunk: Math.max(...samples.map(item => item.bytes)), arrayBuffersLiveDelta, heapUsedLiveDelta, finalArrayBuffersDelta, finalHeapUsedDelta, ms}}));
} catch (error) {
  console.error(error);
  exitCode = 1;
} finally {
  await env.close().catch(() => {});
}
process.exit(exitCode);
