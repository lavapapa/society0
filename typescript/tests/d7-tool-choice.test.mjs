// D7：required-first 的 tool_choice 必须进入真实 model-pi 请求体，且只出现在首轮。
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {setup} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';
import {ModelProviders} from '../.build/core/dist/model-providers/index.js';
import {AgentRuntimes} from '../.build/core/dist/agent-runtimes/index.js';
import AgentRuntimePi from '../.build/core/dist/agent-runtime-pi/index.js';
import InferenceDefaults from '../.build/core/dist/inference-defaults/index.js';
import {DriverRegistry} from '../.build/core/dist/drivers/index.js';
import {DriverLlm} from '../.build/core/dist/driver-llm/index.js';

const model = {id: 'offline', name: 'offline', api: 'openai-completions', provider: 'offline', baseUrl: '', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256};

function completion(message, finish = 'stop') {
    return JSON.stringify({id: 'd7', object: 'chat.completion', created: 1, model: 'offline', choices: [{index: 0, message, finish_reason: finish}], usage: {prompt_tokens: 10, completion_tokens: 2, total_tokens: 12}});
}

async function gateway() {
    const bodies = [], sockets = new Set();
    const server = createServer(async (request, response) => {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const body = JSON.parse(Buffer.concat(chunks).toString());
        bodies.push(body);
        response.writeHead(200, {'content-type': 'application/json'});
        if (bodies.length === 1) {
            response.end(completion({role: 'assistant', content: null, tool_calls: [{id: 'd7-act', type: 'function', function: {name: 'act', arguments: JSON.stringify({action: 'demo.publish', input: {content: 'hi'}})}}]}, 'tool_calls'));
        }
        else {
            response.end(completion({role: 'assistant', content: 'done'}));
        }
    });
    server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const credentials = new InMemoryCredentialStore();
    await credentials.modify('offline', async () => ({type: 'api_key', key: 'offline-fixture-key'}));
    const selected = {...model, baseUrl: 'http://127.0.0.1:' + server.address().port + '/v1'};
    const models = createModels({credentials, authContext: {env: async () => undefined, fileExists: async () => false}});
    models.setProvider(createProvider({id: 'offline', models: [selected], auth: {apiKey: envApiKeyAuth('explicit', [])}, api: completions}));
    return {bodies, server, models, close: async () => { server.closeAllConnections(); for (const socket of sockets) socket.destroy(); await new Promise(resolve => server.close(resolve)); }};
}

async function activate(toolChoice) {
    const run = await setup({migrations: [actorToolsMigration({test: 'd7-tool-choice'})]});
    const gate = await gateway();
    try {
        for (const [plugin, config] of [
            [Prompts, {sections: [{id: 'instructions', text: 'Use act.'}]}],
            [ContextMount, {}], [ExplorationShell, {}], [ActorTools, {}], [ModelProviders, {}],
            [AgentRuntimes, {}], [AgentRuntimePi, {}], [InferenceDefaults, {}], [DriverRegistry, {}]
        ]) await run.host.ctx.plugin(plugin, config);
        let ctx;
        await run.host.ctx.plugin({inject: ['actions', 'modelProviders', 'drivers', 'actorTools', 'threads', 'contextMount', 'prompts', 'database', 'perceptions', 'artifacts', 'attempts', 'agentRuntimes', 'slots'], apply(c) {
            ctx = c;
            c.actions.register({name: 'demo.publish', effect: 'read', inputSchema: {type: 'object', properties: {content: {type: 'string'}}, required: ['content']}, outputSchema: {}, available: async () => true, execute: async (input, context) => ({operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: []})});
            const provider = new PiModelProvider(gate.models, c.attempts, {runId: 'pr07', providerId: 'offline', maxEventBytes: 65536, maxRawBytes: 1048576, maxRequestBytes: 1048576, options: {maxTokens: 256, maxRetries: 0, transport: 'sse'}, timeoutMs: 5000, jsonTimeoutMs: 180000, transport: 'json'});
            c.modelProviders.register('pi/v1', provider);
        }});
        await run.host.ctx.plugin(DriverLlm, {name: 'llm/d7', provider: 'pi/v1', model: 'offline', maxRequests: 4, maxToolCalls: 2, requiredActions: [{name: 'demo.publish', minimumCompleted: 1}], toolChoice});
        const context = run.context('d7-tool-choice');
        const driver = await ctx.drivers.acquire('llm/d7', context);
        const result = await (async () => {
            try {
                return await driver.value.activate({id: 'd7-tool-choice', actorId: context.actorId, momentId: context.momentId, source: 'test', driver: 'llm/d7', input: 'publish'}, context);
            }
            finally { await driver.release(); }
        })();
        return {result, bodies: gate.bodies.map(body => structuredClone(body))};
    }
    finally {
        await gate.close();
        await run.close();
    }
}

test('D7 required-first sends tool_choice=required only in the first physical request', async () => {
    const {result, bodies} = await activate('required-first');
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.equal(bodies.length, 2, JSON.stringify(bodies));
    assert.equal(bodies[0].tool_choice, 'required');
    assert.equal('tool_choice' in bodies[1], false, JSON.stringify(bodies[1]));
});

test('D7 auto never adds tool_choice to a physical request', async () => {
    const {result, bodies} = await activate('auto');
    assert.equal(result.status, 'completed', JSON.stringify(result));
    assert.equal(bodies.length, 2, JSON.stringify(bodies));
    for (const body of bodies) assert.equal('tool_choice' in body, false, JSON.stringify(body));
});
