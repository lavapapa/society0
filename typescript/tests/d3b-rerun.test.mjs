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
const model = {id: 'fake', name: 'fake', api: 'openai-completions', provider: 'fake', baseUrl: 'fake:', reasoning: false, input: ['text'], cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, contextWindow: 8192, maxTokens: 256};
const toolCall = (id, input) => assistant([{type: 'toolCall', id, name: 'act', arguments: {action: 'social.publish', input}}], 'toolUse');
const done = text => assistant([{type: 'text', text}]);
const upstreamError = () => assistant([], 'error', 'MODEL_UPSTREAM_ERROR: 500');
const settlement = operationId => ({physicalRequestId: 'fake-failed-' + operationId, status: 'failed', httpStatus: 500, cause: 'MODEL_UPSTREAM_ERROR'});
const baseOperation = operationId => operationId.replace(/\/r\d+$/, '');
const recoveryOf = operationId => operationId === baseOperation(operationId) ? 0 : Number(/\/r(\d+)$/.exec(operationId)[1]);

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

/** 每个激活的脚本由 (operationId, 恢复次数) 决定；失败请求按 500 结算。 */
class ScriptedUpstream {
  constructor(script) { this.script = script; this.calls = []; }
  async prepare(request, context) {
    const operationId = String(context.operationId), attempt = recoveryOf(operationId);
    this.calls.push({operationId, attempt});
    const fixtures = this.script(operationId, attempt, String(context.actorId));
    const failed = fixtures.some(item => item.stopReason === 'error');
    return selection(fixtures, failed ? [settlement(operationId)] : []);
  }
  async close() {}
}

async function scenario({enabled, script}) {
  const f = await fixture({event: false, migrations: [perceptionMigration({consumer: 'd3b'}), actorToolsMigration({consumer: 'd3b'}), workspaceMigration({consumer: 'd3b'})]});
  const c = f.ctx;
  const upstream = new ScriptedUpstream(script);
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
  await c.plugin(await core('driver-llm'), {provider: 'fake/v1', model: 'fake', perceptions: [], requiredActions: [{name: 'social.publish', minimumCompleted: 1}], maxRequests: 4, maxToolCalls: 2});
  const result = await c.runs.run(new AbortController().signal);
  return {f, result, upstream};
}

async function table(f) {
  const view = await f.ctx.database.snapshot({maxAgeMs: 30000});
  try {
    const posts = await view.query({sql: 'SELECT id,author_id,content FROM social_posts ORDER BY id'}, {maxRows: 100, maxBytes: 65536});
    const receipts = await view.query({sql: 'SELECT operation_id,invocation_id,actor_id,action FROM action_receipts ORDER BY operation_id'}, {maxRows: 100, maxBytes: 65536});
    const [thread] = await view.query({sql: 'SELECT id FROM threads WHERE actor_id=? AND moment_id=?', parameters: ['b', 'm1']}, {maxRows: 1, maxBytes: 1024});
    const rows = thread ? await view.query({sql: 'SELECT seq,message_id,role,payload FROM thread_messages WHERE thread_id=? ORDER BY seq', parameters: [String(thread.id)]}, {maxRows: 1000, maxBytes: 1048576}) : [];
    return {posts: posts.map(row => ({...row})), receipts: receipts.map(row => ({...row})), thread: rows.map(row => ({...row}))};
  } finally { await view.close(); }
}

const textOf = rows => rows.map(row => String(row.payload)).join('\n');
const aPost = () => [toolCall('publish-a', {id: 'post-a', content: 'post a'}), done('done')];
const failedB = first => [first(), upstreamError()];
const retriedB = first => [first(), done('done')];

test('D3b rerun appends a marker and keeps aborted frames alongside the new content', async () => {
  const {f, result} = await scenario({
    enabled: true,
    script: (operationId, attempt) => operationId.includes('tick-1/b')
      ? (attempt === 0 ? failedB(() => toolCall('bad-1', {})) : retriedB(() => toolCall('good-new', {id: 'post-new', content: 'new content'})))
      : aPost(),
  });
  try {
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const facts = await table(f);
    const rows = facts.thread;
    const ids = rows.map(row => String(row.message_id));
    assert.equal(new Set(ids).size, ids.length, 'duplicate thread message ids: ' + JSON.stringify(ids));
    const text = textOf(rows);
    assert.ok(text.includes('bad-1'), 'aborted assistant frame missing: ' + JSON.stringify(rows.map(row => [row.role, row.message_id])));
    assert.ok(text.includes('上一次尝试因上游错误中断'), 'interrupt marker missing');
    assert.ok(text.includes('good-new'), 'new tool call missing');
    assert.ok(text.includes('done'), 'new completion text missing');
    const markers = rows.filter(row => String(row.role) === 'system' && String(row.payload).includes('上一次尝试因上游错误中断'));
    assert.equal(markers.length, 1);
    assert.deepEqual(facts.posts.map(post => post.id), ['post-a', 'post-new']);
  } finally { await f.host.close(); }
});

test('D3b rerun replays a committed action instead of writing it twice', async () => {
  const same = () => toolCall('publish-b', {id: 'post-b', content: 'post b'});
  const {f, result, upstream} = await scenario({
    enabled: true,
    script: (operationId, attempt) => operationId.includes('tick-1/b') ? (attempt === 0 ? failedB(same) : retriedB(same)) : aPost(),
  });
  try {
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const facts = await table(f);
    assert.deepEqual(facts.posts.map(post => post.id), ['post-a', 'post-b']);
    const receipts = facts.receipts.filter(row => row.actor_id === 'b');
    assert.equal(receipts.length, 1, JSON.stringify(receipts));
    assert.ok(textOf(facts.thread).includes('重放'), 'replayed receipt is not visible in the thread');
    assert.ok(upstream.calls.some(call => call.attempt === 1), 'the activation was not re-run in a new namespace');
  } finally { await f.host.close(); }
});

test('D3b rerun completes from the already committed action when the model does not act again', async () => {
  const same = () => toolCall('publish-b', {id: 'post-b', content: 'post b'});
  const {f, result} = await scenario({
    enabled: true,
    script: (operationId, attempt) => operationId.includes('tick-1/b') ? (attempt === 0 ? failedB(same) : [done('already published')]) : aPost(),
  });
  try {
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const facts = await table(f);
    assert.deepEqual(facts.posts.map(post => post.id), ['post-a', 'post-b']);
    assert.equal(facts.receipts.filter(row => row.actor_id === 'b').length, 1);
    assert.ok(textOf(facts.thread).includes('上一次尝试因上游错误中断'));
  } finally { await f.host.close(); }
});

test('D3b rerun reports an input mismatch while still replaying the committed receipt', async () => {
  const {f, result} = await scenario({
    enabled: true,
    script: (operationId, attempt) => operationId.includes('tick-1/b')
      ? (attempt === 0 ? failedB(() => toolCall('publish-b', {id: 'post-b', content: 'post b'})) : retriedB(() => toolCall('publish-b', {id: 'post-b', content: 'different wording'})))
      : aPost(),
  });
  try {
    assert.equal(result.status, 'completed', JSON.stringify(result));
    const facts = await table(f);
    assert.deepEqual(facts.posts.map(post => post.id), ['post-a', 'post-b']);
    assert.equal(facts.receipts.filter(row => row.actor_id === 'b').length, 1);
    const text = textOf(facts.thread);
    assert.ok(text.includes('重放'), 'replay marker missing');
    assert.ok(text.includes('replayed'), 'replayed flag not visible: ' + text.slice(-2000));
    assert.ok(text.includes('inputMismatch'), 'inputMismatch flag not visible');
  } finally { await f.host.close(); }
});

test('D3b autoRecover off never opens a recovery namespace', async () => {
  const same = () => toolCall('publish-b', {id: 'post-b', content: 'post b'});
  const {f, result, upstream} = await scenario({
    enabled: false,
    script: (operationId, attempt) => operationId.includes('tick-1/b') ? (attempt === 0 ? failedB(same) : retriedB(same)) : aPost(),
  });
  try {
    assert.equal(result.status, 'paused');
    assert.equal(result.reason, 'incomplete');
    assert.ok(upstream.calls.every(call => call.attempt === 0), JSON.stringify(upstream.calls));
    const facts = await table(f);
    assert.deepEqual(facts.posts.map(post => post.id), ['post-a', 'post-b']);
    assert.equal(facts.thread.filter(row => String(row.payload).includes('上一次尝试因上游错误中断')).length, 0);
  } finally { await f.host.close(); }
});
