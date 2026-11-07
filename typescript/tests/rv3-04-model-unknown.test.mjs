import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {fixture,extensions} from './pr08-support.mjs';

const modelFor = port => ({id: 'offline', name: 'offline', api: 'openai-completions', provider: 'offline', baseUrl: 'http://127.0.0.1:' + port + '/v1', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256});

async function exercise(run, port, name) {
  const credentials = new InMemoryCredentialStore();
  await credentials.modify('offline', async () => ({type: 'api_key', key: 'fixture-key'}));
  const models = createModels({credentials, authContext: {env: async () => undefined, fileExists: async () => false}});
  models.setProvider(createProvider({id: 'offline', models: [modelFor(port)], auth: {apiKey: envApiKeyAuth('explicit', [])}, api: completions}));
  const provider = new PiModelProvider(models, run.ctx.attempts, {runId: 'pr07', providerId: 'offline', maxEventBytes: 65536, maxRawBytes: 1048576, maxRequestBytes: 1048576, options: {maxTokens: 256, maxRetries: 0, transport: 'sse'}, timeoutMs: 2000});
  const context = run.context(name);
  let selected;
  try {
    selected = await provider.prepare({provider: 'pi/v1', model: 'offline'}, context);
    try {
      const stream = await selected.value.streamFn(selected.value.model, {messages: [{role: 'user', content: 'hi', timestamp: 0}]}, {});
      for await (const _ of stream) {}
    } catch {}
    await selected.value.drain();
    const settlements = [...(selected.value.attemptSettlements ?? [])];
    const probe = await run.ctx.attempts.probe({lineage: run.ctx.attempts.authority().lineage, operationId: context.operationId + '/model/1', attempt: 0});
    return {settlements, probe};
  } finally {
    await selected?.release().catch(() => {});
    await provider.close();
  }
}

async function withServer(handler) {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {port: server.address().port, close: () => new Promise(resolve => server.close(resolve))};
}

const complete = (status, body) => (request, response) => { request.resume(); response.writeHead(status, {'content-type': 'application/json'}); response.end(body); };
const sse = (request, response) => { request.resume(); response.writeHead(200, {'content-type': 'text/event-stream'}); response.write('data: ' + JSON.stringify({id: 'x', object: 'chat.completion.chunk', created: 0, model: 'offline', choices: [{index: 0, delta: {role: 'assistant', content: 'ok'}, finish_reason: 'stop'}]}) + '\n\n'); response.end('data: [DONE]\n\n'); };
const cut = (request, response) => { request.resume(); response.writeHead(200, {'content-type': 'text/event-stream'}); response.write('data: ' + JSON.stringify({id: 'x', object: 'chat.completion.chunk', created: 0, model: 'offline', choices: [{index: 0, delta: {role: 'assistant', content: 'par'}, finish_reason: null}]}) + '\n\n'); setTimeout(() => response.destroy(), 100); };
const open400 = (request, response) => { request.resume(); response.writeHead(400, {'content-type': 'application/json'}); response.write('{"error":'); setTimeout(() => response.destroy(), 500); };

test('RV3-04-T01 model physical outcome follows recorded dispatch facts', async () => {
  const run = await fixture();
  try {
    let server = await withServer(complete(400, JSON.stringify({error: {message: 'bad'}})));
    let result = await exercise(run, server.port, 'rv304-a');
    await server.close();
    assert.equal(result.settlements[0]?.status, 'failed');
    assert.equal(result.settlements[0]?.httpStatus, 400);
    assert.equal(result.probe.record.status, 'settled');
    assert.equal(result.probe.record.result.status, 'failed');
    assert.equal(result.probe.record.result.httpStatus, 400);

    const temp = createServer((_q, s) => s.end());
    await new Promise(resolve => temp.listen(0, '127.0.0.1', resolve));
    const refusedPort = temp.address().port;
    await new Promise(resolve => temp.close(resolve));
    result = await exercise(run, refusedPort, 'rv304-b');
    assert.equal(result.settlements[0]?.status, 'not_dispatched');
    assert.equal(result.probe.record.status, 'settled');
    assert.equal(result.probe.record.result.status, 'not_dispatched');

    server = await withServer(complete(503, '{"error":{"message":"busy"}}'));
    result = await exercise(run, server.port, 'rv304-c');
    await server.close();
    assert.equal(result.settlements[0]?.status, 'failed');
    assert.equal(result.probe.record.status, 'settled');
    assert.equal(result.probe.record.result.status, 'failed');
    assert.match(String(result.probe.record.result.error), /MODEL_UPSTREAM_ERROR/);

    server = await withServer(sse);
    result = await exercise(run, server.port, 'rv304-d');
    await server.close();
    assert.equal(result.settlements[0]?.status, 'response');
    assert.equal(result.probe.record.status, 'settled');

    server = await withServer(cut);
    result = await exercise(run, server.port, 'rv304-e');
    await server.close();
    assert.equal(result.settlements[0]?.status, 'unknown');
    assert.equal(result.probe.record.status, 'unknown');

    server = await withServer(open400);
    result = await exercise(run, server.port, 'rv304-f');
    await server.close();
    assert.equal(result.settlements[0]?.status, 'unknown');
    assert.equal(result.probe.record.status, 'unknown');
  } finally {
    await run.close();
  }
});

test('RV3-04-T02 PiRuntime exposes transport_unknown from settlement facts', async () => {
  const run = await fixture();
  try {
    const context = run.context('rv304-t02');
    const model = modelFor(1);
    const selection = {provider: 'pi/v1', registrationId: 'rv304', model, contextWindow: 8192, options: {maxTokens: 16, maxRetries: 0, transport: 'sse'}, attemptIds: [], attemptSettlements: [{physicalRequestId: 'physical', status: 'unknown', httpStatus: null, cause: 'MODEL_TRANSPORT_UNKNOWN'}], drain: async () => {}, streamFn: () => { throw Error('boom'); }};
    const runtime = new PiRuntime();
    const sink = new OrderedInferenceSink({maxPendingEvents: 8, maxPendingBytes: 65536}, async () => {}, async () => {}, () => {});
    const outcome = await runtime.run({threadId: 't', interactionId: 'i', history: [], prompts: [{role: 'user', content: 'hi', timestamp: 0}], tools: [], actionReceipts: {read: async () => []}, selection, budget: {maxRequests: 1, maxToolCalls: 0}, requiredActions: []}, context, sink, extensions());
    assert.equal(outcome.status, 'incomplete');
    assert.equal(outcome.reason, 'transport_unknown');
    assert.deepEqual(outcome.modelAttempts, selection.attemptSettlements);
    await runtime.drain('done');
  } finally {
    await run.close();
  }
});

test('RV3-04-T01 complete 401/403/404/422 bodies settle failed', async () => {
  const run = await fixture();
  try {
    for (const status of [401, 403, 404, 422]) {
      const server = await withServer(complete(status, JSON.stringify({error: {message: 'status ' + status}})));
      const result = await exercise(run, server.port, 'rv304-4xx-' + status);
      await server.close();
      assert.equal(result.settlements[0]?.status, 'failed');
      assert.equal(result.settlements[0]?.httpStatus, status);
      assert.equal(result.probe.record.status, 'settled');
      assert.equal(result.probe.record.result.status, 'failed');
      assert.equal(result.probe.record.result.httpStatus, status);
      assert.equal(result.probe.record.result.physicalCalls, 1);
    }
  } finally {
    await run.close();
  }
});
