import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';
import {modelOperationId} from '../.build/core/dist/model-pi/selection.js';

for (const [status, code] of [[500, 'MODEL_UPSTREAM_ERROR'], [429, 'MODEL_RATE_LIMITED']]) {
  test(`L1 HTTP ${status} settles as ${code} after bounded retries`, async () => {
    const run = await fixture();
    let requests = 0;
    const server = createServer((request, response) => {
      requests++;
      request.resume();
      request.on('end', () => {
        response.writeHead(status, {'content-type': 'application/json', 'retry-after': '0'});
        response.end(JSON.stringify({error: {message: 'fixture upstream failure', type: status === 429 ? 'rate_limit' : 'server_error'}}));
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let provider, selected;
    try {
      const credentials = new InMemoryCredentialStore();
      await credentials.modify('offline', async () => ({type: 'api_key', key: 'offline-fixture-key'}));
      const model = {id: 'offline', name: 'offline', api: 'openai-completions', provider: 'offline', baseUrl: 'http://127.0.0.1:' + server.address().port + '/v1', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256};
      const models = createModels({credentials, authContext: {env: async () => undefined, fileExists: async () => false}});
      models.setProvider(createProvider({id: 'offline', models: [model], auth: {apiKey: envApiKeyAuth('explicit', [])}, api: completions}));
      const context = run.context('l1-upstream-' + status);
      provider = new PiModelProvider(models, run.ctx.sessions.log, {runId: context.runId, providerId: 'offline', maxEventBytes: 65536, maxRawBytes: 1048576, options: {maxTokens: 64, maxRetries: 2, transport: 'sse'}, timeoutMs: 10000});
      selected = await provider.prepare({session:{actorId:'a',momentId:'m'},provider: 'pi/v1', model: 'offline'}, context);
      const stream = await selected.value.streamFn(selected.value.model, {messages: [{role: 'user', content: 'error fixture', timestamp: 0}]}, {});
      for await (const event of stream) {}
      await selected.value.drain();
      assert.equal(requests, 3);
      const found = await run.ctx.sessions.find({actorId: 'a', momentId: 'm'}, {operationId: modelOperationId(context, 1), attempt: 0});
      assert.equal(found.type, 'attempt/failed');
      assert.match(String(found.data.error), new RegExp(code));
      assert.match(String(found.data.cause ?? found.data.error), new RegExp(code));
    } finally {
      await selected?.release().catch(() => {});
      await provider?.close();
      await new Promise(resolve => server.close(resolve));
      await run.close();
    }
  });
}
