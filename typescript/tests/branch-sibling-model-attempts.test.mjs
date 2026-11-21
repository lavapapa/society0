// Sibling runs branched from one complete point share the lineage attempts authority
// (branch-source BRANCH_ATTEMPT_AUTHORITY). The same activation operationId replayed in
// two sibling runs must claim distinct model attempts instead of colliding on
// attempts(operation_id, attempt).
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';

const model = port => ({id: 'offline', name: 'offline', api: 'openai-completions', provider: 'offline', baseUrl: 'http://127.0.0.1:' + port + '/v1', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256});
const sse = (request, response) => { request.resume(); response.writeHead(200, {'content-type': 'text/event-stream'}); response.write('data: ' + JSON.stringify({id: 'x', object: 'chat.completion.chunk', created: 0, model: 'offline', choices: [{index: 0, delta: {role: 'assistant', content: 'ok'}, finish_reason: 'stop'}]}) + '\n\n'); response.end('data: [DONE]\n\n'); };

async function call(run, port, runId, operationId) {
  const credentials = new InMemoryCredentialStore();
  await credentials.modify('offline', async () => ({type: 'api_key', key: 'fixture-key'}));
  const models = createModels({credentials, authContext: {env: async () => undefined, fileExists: async () => false}});
  models.setProvider(createProvider({id: 'offline', models: [model(port)], auth: {apiKey: envApiKeyAuth('explicit', [])}, api: completions}));
  const provider = new PiModelProvider(models, run.ctx.attempts, {runId, providerId: 'offline', maxEventBytes: 65536, maxRawBytes: 1048576, maxRequestBytes: 1048576, options: {maxTokens: 256, maxRetries: 0, transport: 'sse'}, timeoutMs: 5000});
  const context = {...run.context('sibling'), runId, operationId};
  const selected = await provider.prepare({provider: 'pi/v1', model: 'offline'}, context);
  try {
    let error;
    try {
      const stream = await selected.value.streamFn(selected.value.model, {messages: [{role: 'user', content: 'hi from ' + runId, timestamp: 0}]}, {});
      for await (const _ of stream) {}
      await selected.value.drain();
    } catch (cause) { error = cause; }
    return {error, settlements: [...(selected.value.attemptSettlements ?? [])]};
  } finally {
    await selected.release().catch(() => {});
    await provider.close();
  }
}

test('sibling runs on one lineage claim distinct model attempts for the same activation operationId', async t => {
  const run = await fixture();
  const server = createServer(sse);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { await new Promise(resolve => server.close(resolve)); await run.close(); });
  const port = server.address().port;
  const activation = JSON.stringify(['activation', 'round-robin/2', 'alice', 'round/2', 'llm/v1', 'turn/2']);

  const a = await call(run, port, 'world-a', activation);
  assert.equal(a.error, undefined, String(a.error?.stack ?? a.error));
  const b = await call(run, port, 'world-b', activation);
  assert.equal(b.error, undefined, String(b.error?.stack ?? b.error));
  assert.deepEqual([a, b].map(item => item.settlements.map(s => s.status)), [['response'], ['response']]);
  assert.notEqual(a.settlements[0].physicalRequestId, b.settlements[0].physicalRequestId);

  // Same run replaying the same operation is still refused: send eligibility stays durable per run.
  const again = await call(run, port, 'world-a', activation);
  assert.equal(again.error?.code, 'MODEL_ATTEMPT_EXISTING');
});
