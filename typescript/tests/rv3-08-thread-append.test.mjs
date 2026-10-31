import test from 'node:test';
import assert from 'node:assert/strict';
import {Threads} from '../.build/core/dist/thread/index.js';
import {setup, read, request} from './pr07-support.mjs';

const codeOf = error => error && typeof error === 'object' ? error.code : undefined;

test('RV3-08-T04 append and appendInput branches under real Threads transactions', async () => {
  const env = await setup({threadPlugin: Threads});
  try {
    const thread = await read(env.ctx, view => env.ctx.threads.open('a', 'm', view));
    let sequence = 0;
    const tx = body => env.ctx.database.transaction(env.reservation.lease, request('rv308-append-' + (++sequence)), body);
    const small = await env.ctx.threads.prepare({id: 'append-small', interactionId: thread.interactionId, role: 'user', content: 'small', originalProviderFields: null});
    const large = await env.ctx.threads.prepare({id: 'append-large', interactionId: thread.interactionId, role: 'user', content: 'x'.repeat(65536), originalProviderFields: null});
    assert.equal(large.payload.kind, 'reference');
    await assert.rejects(tx(t => env.ctx.threads.append(t, thread.id, {...small, interactionId: 'wrong'})), error => codeOf(error) === 'THREAD_INTERACTION');
    await tx(t => env.ctx.threads.append(t, thread.id, {id: 'append-raw', interactionId: thread.interactionId, role: 'user', content: 'raw', originalProviderFields: null}));
    await tx(t => env.ctx.threads.append(t, thread.id, large));
    await assert.rejects(tx(t => env.ctx.threads.append(t, thread.id, {id: 'append-oversize', interactionId: thread.interactionId, role: 'user', payload: {kind: 'inline', value: {content: 'x'.repeat(20000)}}, references: []})), error => codeOf(error) === 'THREAD_PREPARE_REQUIRED');
    const next = await env.ctx.threads.prepare({id: 'append-input-next', interactionId: thread.interactionId, role: 'user', content: 'next', originalProviderFields: null});
    const consumer = {consumerId: 'rv308-consumer', cursor: {operationId: 'rv308-append-input'}};
    await tx(t => env.ctx.threads.appendInput(t, {threadId: thread.id, expectedConsumer: null, nextConsumer: consumer, message: small, contexts: []}));
    await assert.rejects(tx(t => env.ctx.threads.appendInput(t, {threadId: thread.id, expectedConsumer: null, nextConsumer: {consumerId: 'rv308-consumer', cursor: {operationId: 'stale'}}, message: small, contexts: []})), error => codeOf(error) === 'THREAD_CONSUMER_STALE');
    await tx(t => env.ctx.threads.appendInput(t, {threadId: thread.id, expectedConsumer: consumer, nextConsumer: {consumerId: 'rv308-consumer', cursor: {operationId: 'rv308-next'}}, message: next, contexts: [{kind: 'inline', value: {marker: true}}]}));
    const after = await read(env.ctx, view => env.ctx.threads.open('a', 'm', view));
    assert.ok(BigInt(after.headSeq) > BigInt(thread.headSeq));
    console.log(JSON.stringify({rv308Append: {headBefore: thread.headSeq, headAfter: after.headSeq, sequence}}));
  } finally {
    await env.close();
  }
});
