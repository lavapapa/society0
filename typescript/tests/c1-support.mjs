import {createAssistantMessageEventStream} from '@earendil-works/pi-ai';
import {setup} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools, actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';
import {ModelProviders} from '../.build/core/dist/model-providers/index.js';
import {AgentRuntimes} from '../.build/core/dist/agent-runtimes/index.js';
import AgentRuntimePi from '../.build/core/dist/agent-runtime-pi/index.js';
import InferenceDefaults from '../.build/core/dist/inference-defaults/index.js';
import {DriverRegistry} from '../.build/core/dist/drivers/index.js';
import {DriverLlm} from '../.build/core/dist/driver-llm/index.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';

const usage = {input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}};
export const assistant = (content, stopReason = 'stop') => ({role: 'assistant', content, api: 'openai-completions', provider: 'replay', model: 'driver', usage, stopReason, timestamp: Date.now()});

function deltaStream(text, {failAfter} = {}) {
    const final = assistant([{type: 'text', text}]);
    if (failAfter !== undefined) {
        return {
            async *[Symbol.asyncIterator]() {
                let partial = {...final, content: [{type: 'text', text: ''}]};
                yield {type: 'start', partial};
                let seen = '';
                for (let index = 0; index < text.length; index++) {
                    seen += text[index];
                    partial = {...partial, content: [{type: 'text', text: seen}]};
                    if (index >= failAfter) throw Error('C1_STREAM_CRASH');
                    yield {type: 'text_delta', contentIndex: 0, delta: text[index], partial};
                }
                yield {type: 'done', reason: 'stop', message: final};
            },
            result: async () => final
        };
    }
    const stream = createAssistantMessageEventStream();
    let partial = {...final, content: [{type: 'text', text: ''}]};
    stream.push({type: 'start', partial});
    let seen = '';
    for (const character of text) {
        seen += character;
        partial = {...partial, content: [{type: 'text', text: seen}]};
        stream.push({type: 'text_delta', contentIndex: 0, delta: character, partial});
    }
    stream.push({type: 'text_end', contentIndex: 0, content: seen, partial});
    stream.push({type: 'done', reason: 'stop', message: final});
    stream.end();
    return stream;
}

export async function driverFixture({text = 'x'.repeat(300), crashAfter} = {}) {
    const run = await setup({migrations: [actorToolsMigration({test: 'c1-batching'})]});
    for (const [plugin, config] of [
        [Prompts, {sections: [{id: 'instructions', text: 'Answer with text.'}]}],
        [ContextMount, {}],
        [ExplorationShell, {}],
        [ActorTools, {}],
        [ModelProviders, {}],
        [AgentRuntimes, {}],
        [AgentRuntimePi, {}],
        [InferenceDefaults, {}],
        [DriverRegistry, {}]
    ]) await run.host.ctx.plugin(plugin, config);
    let ctx;
    await run.host.ctx.plugin({inject: ['actions', 'modelProviders', 'drivers', 'actorTools', 'threads', 'contextMount', 'prompts', 'database', 'perceptions', 'artifacts', 'attempts', 'agentRuntimes', 'slots'], apply(c) { ctx = c; }});
    const provider = new ReplayProvider({driver: [assistant([{type: 'text', text}])]});
    let calls = 0;
    const prepare = provider.prepare.bind(provider);
    provider.prepare = async (...args) => {
        const selected = await prepare(...args);
        calls++;
        const fail = crashAfter !== undefined && calls === 1;
        return {value: {...selected.value, streamFn: () => deltaStream(text, {failAfter: fail ? crashAfter : undefined})}, release: () => selected.release()};
    };
    ctx.modelProviders.register('replay/v1', provider);
    await run.host.ctx.plugin(DriverLlm, {name: 'llm/c1', provider: 'replay/v1', model: 'driver', maxRequests: 2, maxToolCalls: 0});
    return {run, ctx};
}

export async function threadRows(ctx, moment = 'm') {
    const view = await ctx.database.snapshot({maxAgeMs: 30000});
    try {
        const [thread] = await view.query({sql: 'SELECT id FROM threads WHERE actor_id=? AND moment_id=?', parameters: ['a', moment]}, {maxRows: 1, maxBytes: 1024});
        if (!thread) return [];
        const rows = await view.query({sql: 'SELECT seq,role,message_id FROM thread_messages WHERE thread_id=? ORDER BY seq', parameters: [String(thread.id)]}, {maxRows: 10000, maxBytes: 1048576});
        return rows.map(row => ({seq: String(row.seq), role: String(row.role), message_id: String(row.message_id)}));
    } finally {
        await view.close();
    }
}

export const eventCommits = result => result.requiredOperations.filter(id => id.includes('/event/') || id.includes('/events/')).length;
