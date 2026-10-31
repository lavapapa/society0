import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {EmbeddingQueue} from '../.build/core/dist/embedding/broker.js';
import {HttpEmbeddingProvider} from '../.build/core/dist/embedding-http/provider.js';

const limits = {maxTextBytes: 1024, maxPendingBytes: 1 << 20, maxBatchItems: 1, maxBatchBytes: 1024, maxWaitMs: 1, maxCacheItems: 0, maxCacheBytes: 0, scheduling: 'fifo'};
const identity = {provider: 'p', configId: 'c', model: 'm', dimensions: 2};

function fakeLedger(options = {}) {
  const calls = {claim: 0, settle: 0, markUnknown: 0};
  let sequence = 0;
  const ledger = {
    calls,
    authority: () => ({lineage: 'l'}),
    evidenceChunkBytes: 1024,
    async stageEvidence(source, mediaType) {
      let bytes = 0;
      for await (const chunk of source) bytes += chunk.byteLength;
      sequence++;
      return {id: 'artifact-' + sequence, bytes: String(bytes), mediaType};
    },
    async claim() {
      calls.claim++;
      if (options.claim) return options.claim();
      return {status: 'send-permitted', identity, physicalRequestId: 'physical'};
    },
    async settle(_identity, result) {
      calls.settle++;
      this.lastSettled = result;
      if (options.settle) await options.settle(result);
    },
    async markUnknown(_identity, reason) {
      calls.markUnknown++;
      if (options.markUnknown) throw options.markUnknown;
    },
  };
  return ledger;
}

const context = () => ({runId: 'r', operationId: 'op', signal: new AbortController().signal});

test('NEW-3-T01 ledger failure surfaces and later work is refused', async () => {
  const ledger = fakeLedger({markUnknown: Error('LEDGER_DOWN')});
  const provider = {identity, async embed() { throw Error('NET'); }, async close() {}};
  const queue = new EmbeddingQueue('r', provider, limits, ledger);
  try {
    let error;
    await assert.rejects(queue.embed({identity, texts: ['x']}, context()), cause => { error = cause; return true; });
    assert.equal(error.code, 'EMBEDDING_LEDGER_ERROR');
    assert.ok(error.errors?.some(item => String(item).includes('NET')));
    assert.ok(error.errors?.some(item => String(item).includes('LEDGER_DOWN')));
    await assert.rejects(queue.embed({identity, texts: ['y']}, context()), cause => cause.code === 'EMBEDDING_LEDGER_ERROR');
  } finally {
    await queue.close();
  }
});

test('NEW-3-T01b no ledger write without claim', async () => {
  const ledger = fakeLedger({claim: () => ({status: 'existing', record: {status: 'pending'}})});
  const provider = {identity, async embed() { throw Error('must not run'); }, async close() {}};
  const queue = new EmbeddingQueue('r', provider, limits, ledger);
  try {
    await assert.rejects(queue.embed({identity, texts: ['x']}, context()));
    assert.equal(ledger.calls.markUnknown, 0);
    assert.equal(ledger.calls.settle, 0);
  } finally {
    await queue.close();
  }
});

async function serverWith(responses) {
  let index = 0;
  const server = createServer((request, response) => {
    request.resume();
    const status = responses[Math.min(index++, responses.length - 1)];
    if (status === 200) {
      response.writeHead(200, {'content-type': 'application/json'});
      response.end(JSON.stringify({data: [{index: 0, embedding: [1, 0]}], usage: {tokens: 1}}));
    } else {
      response.writeHead(status, {'content-type': 'application/json'});
      response.end(JSON.stringify({error: {message: 'status ' + status}}));
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, calls: () => index};
}

test('NEW-3-T02 HTTP 400 settles failed and queue continues', async () => {
  const ledger = fakeLedger();
  const {server} = await serverWith([400, 200]);
  const provider = new HttpEmbeddingProvider({identity, url: 'http://127.0.0.1:' + server.address().port + '/embed', credentialProvider: '', timeoutMs: 1000, maxResponseBytes: 65536, maxRequestBytes: 4096}, undefined, ledger);
  const queue = new EmbeddingQueue('r', provider, limits, ledger);
  try {
    await assert.rejects(queue.embed({identity, texts: ['x']}, context()));
    assert.equal(ledger.calls.settle, 1);
    assert.equal(ledger.calls.markUnknown, 0);
    assert.equal(ledger.lastSettled?.status, 'failed');
    assert.equal(ledger.lastSettled?.httpStatus, 400);
    const output = await queue.embed({identity, texts: ['y']}, context());
    assert.deepEqual(output[0].vector, [1, 0]);
  } finally {
    await queue.close();
    await new Promise(resolve => server.close(resolve));
  }
});

test('NEW-3-T02b 503 is unknown and poisons the queue', async () => {
  const ledger = fakeLedger();
  const {server, calls} = await serverWith([503]);
  const provider = new HttpEmbeddingProvider({identity, url: 'http://127.0.0.1:' + server.address().port + '/embed', credentialProvider: '', timeoutMs: 1000, maxResponseBytes: 65536, maxRequestBytes: 4096}, undefined, ledger);
  const queue = new EmbeddingQueue('r', provider, limits, ledger);
  try {
    await assert.rejects(queue.embed({identity, texts: ['x']}, context()));
    assert.equal(ledger.calls.markUnknown, 1);
    await assert.rejects(queue.embed({identity, texts: ['y']}, context()));
    assert.equal(calls(), 1);
  } finally {
    await queue.close();
    await new Promise(resolve => server.close(resolve));
  }
});
