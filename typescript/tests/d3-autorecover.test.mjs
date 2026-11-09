import test from 'node:test';
import assert from 'node:assert/strict';
import {createAssistantMessageEventStream} from '@earendil-works/pi-ai';
import {fixture, runtime} from './pr10-support.mjs';
import Workspace, {workspaceMigration} from '../.build/extras/dist/workspace/index.js';
import {perceptionMigration} from '../.build/core/dist/perception/index.js';
import {actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';

const core = async name => (await import('../.build/core/dist/' + name + '/index.js')).default;
const usage = {input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}};
const assistant = (content, stopReason = 'stop', errorMessage) => ({role: 'assistant', content, api: 'openai-completions', provider: 'fake', model: 'fake', usage, stopReason, ...(errorMessage ? {errorMessage} : {}), timestamp: 1});
const publish = id => assistant([{type: 'toolCall', id: 'publish-' + id, name: 'act', arguments: {action: 'social.publish', input: {id, content: 'post ' + id}}}], 'toolUse');
const model = {id: 'fake', name: 'fake', api: 'openai-completions', provider: 'fake', baseUrl: 'fake:', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256};

function selection(fixtures, attempts = []) {
  let cursor = 0;
  const value = {
    provider: 'fake/v1', registrationId: 'fake/v1', model, contextWindow: 8192,
    options: {maxRetries: 0, transport: 'sse', maxTokens: 256},
    attemptIds: attempts.map(item => item.physicalRequestId), attemptSettlements: attempts,
    streamFn() {
      const message = structuredClone(fixtures[Math.min(cursor++, fixtures.length - 1)]);
      const stream = createAssistantMessageEventStream();
      stream.push({type: 'start', partial: message});
      if (message.stopReason === 'error') stream.push({type: 'error', reason: 'error', error: message});
      else { stream.push({type: 'done', reason: message.stopReason, message}); }
      stream.end();
      return stream;
    },
  };
  return {value, release: async () => {}};
}

class FakeUpstream {
  constructor({fail = true} = {}) { this.fail = fail; this.calls = new Map(); }
  async prepare(request, context) {
    const calls = this.calls.get(context.operationId) ?? 0;
    this.calls.set(context.operationId, calls + 1);
    if (this.fail && calls === 0 && !/\/r\d+$/.test(context.operationId) && context.operationId.includes('tick-1/b')) {
      const settlement = {physicalRequestId: 'fake-failed-' + context.operationId, status: 'failed', httpStatus: 500, cause: 'MODEL_UPSTREAM_ERROR'};
      return selection([assistant([], 'error', 'MODEL_UPSTREAM_ERROR: 500')], [settlement]);
    }
    const actor = context.actorId;
    return selection([publish('post-' + actor), assistant([{type: 'text', text: 'done'}])]);
  }
  async close() {}
}

async function scenario({enabled, fail}) {
  const f = await fixture({event: false, migrations: [perceptionMigration({consumer: 'd3'}), actorToolsMigration({consumer: 'd3'}), workspaceMigration({consumer: 'd3'})]});
  const c = f.ctx;
  const upstream = new FakeUpstream({fail});
  await runtime(f, {
    runConfig: {autoRecover: {enabled, maxAttempts: 2, backoffMs: 1}},
    sourceConfig: {proposals: [
      {id: 'tick-1', planKind: 'staged/1', payload: {nextCursor: '1', phases: [
        {id: 'a', activations: [{id: 'tick-1/a', actorId: 'a', momentId: 'm1', source: 'test', driver: 'llm/v1', input: {}}]},
        {id: 'b', activations: [{id: 'tick-1/b', actorId: 'b', momentId: 'm1', source: 'test', driver: 'llm/v1', input: {}}]},
      ]}},
      {id: 'tick-2', planKind: 'staged/1', payload: {nextCursor: '2', phases: []}},
    ]},
  });
  await c.plugin(Workspace);
  for (const name of ['perception', 'exploration', 'mount-workspace', 'mount-results', 'plain', 'prompts', 'mount-context', 'exploration-shell', 'actor-tools', 'model-providers', 'agent-runtimes', 'agent-runtime-pi', 'inference-defaults']) {
    await c.plugin(await core(name), name === 'prompts' ? {sections: [{id: 'social', text: 'Publish the requested social post, then finish.'}]} : {});
  }
  await c.plugin({inject: ['modelProviders'], apply(ctx) { ctx.modelProviders.register('fake/v1', upstream); }});
  await c.plugin(await core('driver-llm'), {provider: 'fake/v1', model: 'fake', perceptions: [], requiredActions: [{name: 'social.publish', minimumCompleted: 1}], maxRequests: 3, maxToolCalls: 1});
  const result = await c.runs.run(new AbortController().signal);
  return {f, result, upstream};
}

async function facts(f) {
  const view = await f.ctx.database.snapshot({maxAgeMs: 30000});
  try {
    const posts = await view.query({sql: 'SELECT id,author_id,content FROM social_posts ORDER BY id'}, {maxRows: 100, maxBytes: 65536});
    const receipts = await view.query({sql: 'SELECT operation_id,actor_id,action FROM action_receipts ORDER BY operation_id'}, {maxRows: 100, maxBytes: 65536});
    return {posts: posts.map(row => ({...row})), receipts: receipts.map(row => ({...row, operation_id: String(row.operation_id).replace(/\/r\d+(?=\/tool\/)/, '')}))};
  } finally { await view.close(); }
}

test('D3-P3B autoRecover is off by default and a retryable pause stays paused', async () => {
  const {f, result} = await scenario({enabled: false, fail: true});
  try {
    assert.equal(result.status, 'paused');
    assert.equal(result.reason, 'incomplete');
    const table = await facts(f);
    assert.deepEqual(table.posts.map(post => post.id), ['post-a']);
  } finally { await f.host.close(); }
});

test('D3-P3B autoRecover resumes the tick without duplicating completed writes', async () => {
  const {f, result, upstream} = await scenario({enabled: true, fail: true});
  try {
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const table = await facts(f);
    assert.deepEqual(table.posts.map(post => post.id), ['post-a', 'post-b']);
    const actorA = table.receipts.filter(row => row.actor_id === 'a');
    assert.equal(actorA.length, 1);
    const actorB = table.receipts.filter(row => row.actor_id === 'b');
    assert.equal(actorB.length, 1);
    const calls = [...upstream.calls];
    assert.equal(calls.find(([operation]) => operation.includes('tick-1/a'))?.[1], 1);
    assert.equal(calls.filter(([operation]) => operation.includes('tick-1/b')).reduce((sum, [, count]) => sum + count, 0), 2);
    const events = f.host.diagnostics.snapshot().errors.filter(error => error.code === 'RUN_AUTO_RECOVER');
    assert.equal(events.length, 1);
    const event = JSON.parse(events[0].message);
    assert.equal(event.attempt, 1);
    assert.equal(event.reason, 'provider_error');
    assert.equal(event.frontier, '0');
  } finally { await f.host.close(); }
});

test('D3-P3B recovered world equals a clean run table for table', async () => {
  const recovered = await scenario({enabled: true, fail: true});
  const clean = await scenario({enabled: true, fail: false});
  try {
    assert.equal(recovered.result.status, 'completed');
    assert.equal(clean.result.status, 'completed');
    assert.deepEqual(await facts(recovered.f), await facts(clean.f));
  } finally { await recovered.f.host.close(); await clean.f.host.close(); }
});
