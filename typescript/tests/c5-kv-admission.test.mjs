// C5：线程策略的 session_id 进入请求体 metadata；KV 准入按 token 预算限制在途激活。
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels, createProvider, InMemoryCredentialStore, envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';
import {setup, request} from './pr07-support.mjs';
import {driverFixture, assistant} from './c1-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import ActivationMailbox from '../.build/core/dist/activation-mailbox/index.js';
import ActivationPolicyDefault from '../.build/core/dist/activation-policy-default/index.js';
import Activation from '../.build/core/dist/activation/index.js';

const model = {id: 'offline', name: 'offline', api: 'openai-completions', provider: 'offline', baseUrl: '', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256};

async function gateway(run, {handler} = {}) {
    const received = [], sockets = new Set();
    const server = createServer(async (request, response) => {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const body = Buffer.concat(chunks).toString();
        received.push({body, headers: request.headers});
        await handler(body, response);
    });
    server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const credentials = new InMemoryCredentialStore();
    await credentials.modify('offline', async () => ({type: 'api_key', key: 'offline-fixture-key'}));
    const selectedModel = {...model, baseUrl: 'http://127.0.0.1:' + server.address().port + '/v1'};
    const models = createModels({credentials, authContext: {env: async () => undefined, fileExists: async () => false}});
    models.setProvider(createProvider({id: 'offline', models: [selectedModel], auth: {apiKey: envApiKeyAuth('explicit', [])}, api: completions}));
    const provider = new PiModelProvider(models, run.ctx.attempts, {runId: 'pr07', providerId: 'offline', maxEventBytes: 65536, maxRawBytes: 1048576, maxRequestBytes: 1048576, options: {maxTokens: 256, maxRetries: 0, transport: 'sse'}, timeoutMs: 5000, jsonTimeoutMs: 180000, transport: 'json'});
    return {provider, received, close: async () => { await provider.close(); server.closeAllConnections(); for (const socket of sockets) socket.destroy(); await new Promise(resolve => server.close(resolve)); }};
}

test('C5 model-pi sends the thread session_id in the request body metadata', async t => {
    const run = await fixture();
    t.after(() => run.close());
    const gate = await gateway(run, {handler: async (_body, response) => {
        response.writeHead(200, {'content-type': 'application/json'});
        response.end(JSON.stringify({id: 'c5', object: 'chat.completion', created: 1, model: 'offline', choices: [{index: 0, message: {role: 'assistant', content: 'ok'}, finish_reason: 'stop'}], usage: {prompt_tokens: 3, completion_tokens: 2, total_tokens: 5}}));
    }});
    t.after(() => gate.close());
    const context = run.context('c5-session');
    const selected = await gate.provider.prepare({provider: 'pi/v1', model: 'offline', sessionId: 'thread/actor/a', metadata: {session_id: 'thread/actor/a'}}, context);
    try {
        const stream = await selected.value.streamFn(selected.value.model, {messages: [{role: 'user', content: 'hi', timestamp: 0}]}, {});
        for await (const _ of stream) { /* drain */ }
        await stream.result();
        await selected.value.drain();
    }
    finally {
        await selected.release();
    }
    const body = JSON.parse(gate.received[0].body);
    assert.deepEqual(body.metadata, {session_id: 'thread/actor/a'});
});

test('C5 driver passes the thread identity as the model session', async t => {
    const requests = [];
    const {run, ctx} = await driverFixture({
        messages: [assistant([{type: 'text', text: 'ok'}])],
        onPrepare: value => requests.push(value)
    });
    t.after(() => run.close());
    const context = run.context('c5-driver-session');
    const driver = await ctx.drivers.acquire('llm/c1', context);
    try {
        const result = await driver.value.activate({id: 'c5-driver-session', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/c1', input: 'session'}, context);
        assert.equal(result.status, 'completed', JSON.stringify(result));
    }
    finally {
        await driver.release();
    }
    assert.equal(requests.length, 1);
    assert.ok(requests[0].sessionId);
    assert.deepEqual(requests[0].metadata, {session_id: requests[0].sessionId});
});

test('C5 KV admission keeps in-flight token estimates within the replica budget', async t => {
    const run = await setup();
    t.after(() => run.close());
    const ctx = run.ctx;
    const ids = ['c', 'd', 'e', 'f', 'g', 'h'];
    await ctx.database.transaction(run.reservation.lease, request('c5-actors'), async tx => {
        for (const id of ids) await ctx.actors.create(tx, {id, attributes: {}});
    });
    let active = 0, peak = 0;
    await ctx.plugin(Drivers);
    ctx.get('drivers').register('c5', {async activate() {
        active++;
        peak = Math.max(peak, active);
        await new Promise(resolve => setTimeout(resolve, 60));
        active--;
        return {status: 'completed', value: null, evidence: {}, requiredOperations: []};
    }});
    await ctx.plugin(ActivationMailbox);
    await ctx.plugin(ActivationPolicyDefault);
    await ctx.plugin(Activation, {capacity: 16, admission: {kind: 'kv', replicas: 1, tokensPerReplica: 2000, utilization: 1}});
    const context = {...run.context('c5-kv'), lease: run.reservation.lease};
    for (const [index, actorId] of ids.entries()) {
        await ctx.get('activation').submit({id: 'c5-kv-' + index, actorId, momentId: 'm', source: 'test', driver: 'c5', input: {index}, estimatedTokens: 1000}, context);
    }
    const results = await ctx.get('activation').drain(context);
    assert.equal(results.length, ids.length);
    assert.ok(results.every(result => result.status === 'completed'));
    assert.equal(peak, 2, 'two 1000-token activations fit one 2000-token replica');
});
