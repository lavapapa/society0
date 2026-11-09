import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,envApiKeyAuth,InMemoryCredentialStore} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';

const completion = (model) => JSON.stringify({
  id: 'completion', object: 'chat.completion', created: 1, model: model.id,
  choices: [{index: 0, message: {role: 'assistant', content: 'ok'}, finish_reason: 'stop'}],
  usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2},
});

async function upstream(statuses, modelId = 'offline') {
  let index = 0;
  const server = createServer((request, response) => {
    request.resume();
    request.on('end', () => {
      const status = statuses[Math.min(index++, statuses.length - 1)];
      if (status === 200) {
        response.writeHead(200, {'content-type': 'application/json'});
        response.end(completion({id: modelId}));
      } else {
        response.writeHead(status, {'content-type': 'application/json'});
        response.end(JSON.stringify({error: {message: 'fixture upstream failure', type: status === 429 ? 'rate_limit' : 'server_error'}}));
      }
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, calls: () => index};
}

async function run(retry, statuses, operationId) {
  const env = await fixture();
  const {server, calls} = await upstream(statuses);
  let provider, selected;
  try {
    const model = {id: 'offline', name: 'offline', api: 'openai-completions', provider: 'offline', baseUrl: 'http://127.0.0.1:' + server.address().port + '/v1', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256};
    const credentials = new InMemoryCredentialStore();
    await credentials.modify('offline', async () => ({type: 'api_key', key: 'offline-fixture-key'}));
    const models = createModels({credentials, authContext: {env: async () => undefined, fileExists: async () => false}});
    models.setProvider(createProvider({id: 'offline', models: [model], auth: {apiKey: envApiKeyAuth('explicit', [])}, api: completions}));
    const context = env.context(operationId);
    provider = new PiModelProvider(models, env.ctx.attempts, {runId: context.runId, providerId: 'offline', maxEventBytes: 65536, maxRawBytes: 1048576, maxRequestBytes: 1048576, retry, options: {maxTokens: 64, maxRetries: 2, transport: 'json'}, jsonTimeoutMs: 10000});
    selected = await provider.prepare({provider: 'pi/v1', model: 'offline'}, context);
    const stream = await selected.value.streamFn(selected.value.model, {messages: [{role: 'user', content: 'retry fixture', timestamp: 0}]}, {});
    for await (const event of stream) {}
    await selected.value.drain();
    return {env, calls, selected, context};
  } finally {
    await selected?.release().catch(() => {});
    await provider?.close();
    await new Promise(resolve => server.close(resolve));
  }
}

test('D3-P3A upstream 500 retries with growing backoff and records each attempt', async () => {
  const retry = {baseMs: 5, maxMs: 20, jitter: 0};
  const {env, calls, selected} = await run(retry, [500, 500, 200], 'd3-retry-upstream');
  try {
    assert.equal(calls(), 3);
    const lineage = env.ctx.attempts.authority().lineage;
    const records = [];
    for (const attempt of [0, 1, 2]) {
      const found = await env.ctx.attempts.probe({lineage, operationId: 'd3-retry-upstream/model/1', attempt});
      assert.equal(found.status, 'found');
      records.push(found.record);
    }
    assert.equal(records[0].result.status, 'failed');
    assert.equal(records[0].result.cause, 'MODEL_UPSTREAM_ERROR');
    assert.equal(records[1].result.status, 'failed');
    assert.equal(records[1].result.retryOf, records[0].physicalRequestId);
    assert.equal(records[2].result.status, 'response');
    assert.equal(records[2].result.retryOf, records[1].physicalRequestId);
    assert.deepEqual([records[1].result.retryDelayMs, records[2].result.retryDelayMs], [5, 10]);
    assert.deepEqual([records[0].result.retryNextDelayMs, records[1].result.retryNextDelayMs], [5, 10]);
    assert.deepEqual(selected.value.attemptSettlements.map(item => item.status), ['failed', 'failed', 'response']);
  } finally {
    await env.close();
  }
});

test('D3-P3A 4xx is not retried', async () => {
  const {env, calls} = await run({baseMs: 1, maxMs: 2, jitter: 0}, [400], 'd3-retry-client');
  try {
    assert.equal(calls(), 1);
    const found = await env.ctx.attempts.probe({lineage: env.ctx.attempts.authority().lineage, operationId: 'd3-retry-client/model/1', attempt: 1});
    assert.equal(found.status, 'absent');
  } finally {
    await env.close();
  }
});
