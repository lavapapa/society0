import test from 'node:test';
import assert from 'node:assert/strict';
import {EmbeddingQueue} from '../.build/core/dist/embedding/broker.js';
import {HttpEmbeddingProvider} from '../.build/core/dist/embedding-http/provider.js';

const limits = {maxTextBytes: 1024, maxPendingBytes: 1 << 20, maxBatchItems: 1, maxBatchBytes: 1024, maxWaitMs: 1, maxCacheItems: 0, maxCacheBytes: 0, scheduling: 'fifo'};
const identity = {provider: 'p', configId: 'c', model: 'm', dimensions: 2};
const context = () => ({runId: 'r', operationId: 'op', signal: new AbortController().signal});
const recorder = () => { const events = []; return {events, append: (key, type, data) => events.push({key, type, data})}; };
function fetchSequence(responses) {
  let index = 0;
  const fetch = async () => {
    const status = responses[Math.min(index++, responses.length - 1)];
    if (status === 200) return new Response(JSON.stringify({data: [{index: 0, embedding: [1, 0]}], usage: {tokens: 1}}), {status: 200, headers: {'content-type': 'application/json'}});
    return new Response(JSON.stringify({error: {message: 'status ' + status}}), {status, headers: {'content-type': 'application/json'}});
  };
  return {fetch, calls: () => index};
}
const provider = fetch => new HttpEmbeddingProvider({identity, url: 'http://127.0.0.1:9/embed', credentialProvider: '', timeoutMs: 1000, maxResponseBytes: 65536, maxRequestBytes: 4096, fetch}, undefined);

test('NEW-3 embedding 503 retries with backoff and records each attempt in the system session', async () => {
  const sessions = recorder(), wire = fetchSequence([503, 200]);
  const queue = new EmbeddingQueue('r', provider(wire.fetch), limits, sessions, {maxRetries: 2, baseMs: 1, maxMs: 1});
  try {
    const output = await queue.embed({identity, texts: ['x']}, context());
    assert.deepEqual(output[0].vector, [1, 0]);
    assert.equal(wire.calls(), 2);
    assert.deepEqual(sessions.events.map(event => [event.type, event.data.attempt]), [['attempt/failed', 0], ['embedding', 1]]);
    assert.deepEqual(sessions.events[0].key, {actorId: '@system', momentId: 'embedding/p'});
    assert.equal(sessions.events[0].data.httpStatus, 503);
  } finally {
    await queue.close();
  }
});

test('NEW-3 exhausted retries reject only that batch; the queue keeps serving', async () => {
  const sessions = recorder(), wire = fetchSequence([400, 200]);
  const queue = new EmbeddingQueue('r', provider(wire.fetch), limits, sessions, {maxRetries: 0, baseMs: 1, maxMs: 1});
  try {
    await assert.rejects(queue.embed({identity, texts: ['x']}, context()));
    const output = await queue.embed({identity, texts: ['y']}, context());
    assert.deepEqual(output[0].vector, [1, 0]);
    assert.deepEqual(sessions.events.map(event => event.type), ['attempt/failed', 'embedding']);
  } finally {
    await queue.close();
  }
});
