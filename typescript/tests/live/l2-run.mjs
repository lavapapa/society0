#!/usr/bin/env node
/**
 * L2 真实 LLM 试点：20 主体 × 5 tick，热目录 + 每 tick 镜像，带看门狗。
 *
 * 用法：
 *   TMPDIR=/var/tmp/society0-next-tmp node tests/live/l2-run.mjs \
 *     --hot-root /var/tmp/society0-next-l2-20261107 --mirror-root <W>/runs/l2-20261107
 * 续跑（从镜像恢复）：
 *   ... --restore-from <W>/runs/l2-20261107 --hot-root /var/tmp/society0-next-l2-20261107-resume
 *
 * 只调用 127.0.0.1:4000；key 只进 0600 临时文件，不进日志/证据。
 */
import assert from 'node:assert/strict';
import {appendFile, chmod, cp, lstat, mkdir, readFile, rename, rm, statfs, symlink, unlink, writeFile} from 'node:fs/promises';
import {appendFileSync, existsSync} from 'node:fs';
import {join, dirname, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {performance} from 'node:perf_hooks';
import {DatabaseSync} from 'node:sqlite';
import {estimateSlots} from './support.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const W = join(here, '..', '..', '..', '..', '..');
const gatewayEnv = process.env.L2_GATEWAY_ENV || join(W, '..', '..', 'services', 'litellm', '.env');
const modelId = 'Qwen3.6-35B-A3B-FP8';
const ticks = 5;
const actorCount = 20;
const maxRequestsPerTick = 500;
const tickStallMs = 10 * 60 * 1000;
const errorWindowMs = 5 * 60 * 1000;

const {values} = parseArgs({options: {
  'hot-root': {type: 'string'},
  'mirror-root': {type: 'string'},
  'restore-from': {type: 'string'},
  'start-tick': {type: 'string'},
  'end-tick': {type: 'string'},
  'reuse-hot': {type: 'boolean', default: false},
  'run-id': {type: 'string', default: 'l2'},
}});
assert.ok(values['hot-root'], '--hot-root required');
assert.ok(values['mirror-root'], '--mirror-root required');
const hotRoot = resolve(values['hot-root']);
const mirrorRoot = resolve(values['mirror-root']);
const runId = values['run-id'];
const endTick = Number(values['end-tick'] ?? ticks);
const evidence = join(W, 'evidence', 'l2');
const logs = join(W, 'evidence', 'logs');
await mkdir(evidence, {recursive: true});
await mkdir(logs, {recursive: true});
await mkdir(mirrorRoot, {recursive: true});
const liveLog = join(evidence, 'live.log');
const progressLog = join(evidence, 'progress.log');
const watchdogLog = join(evidence, 'watchdog.log');
const requestsPath = join(evidence, 'requests.json');
const requestsJsonlPath = join(evidence, 'requests.jsonl');
const metricsPath = join(evidence, 'metrics.jsonl');
const ticksPath = join(evidence, 'ticks.json');
if (!values['restore-from']) {
  await writeFile(liveLog, '');
  await writeFile(progressLog, '');
  await writeFile(requestsPath, '[]');
  await writeFile(requestsJsonlPath, '');
  await writeFile(metricsPath, '');
  await writeFile(ticksPath, '[]');
}
await appendFile(watchdogLog, `# watchdog start ${new Date().toISOString()} hot=${hotRoot} mirror=${mirrorRoot}\n`);

const log = async message => { await appendFile(liveLog, message + '\n').catch(() => {}); console.error(message); };
const progress = async message => { const line = `${new Date().toISOString()} ${message}`; await appendFile(progressLog, line + '\n').catch(() => {}); await log(`PROGRESS ${line}`); };
const watchdog = async reason => { const line = `${new Date().toISOString()} WATCHDOG=${reason}`; await appendFile(watchdogLog, line + '\n').catch(() => {}); await progress(line); };

let exiting = false;
const finish = async code => {
  if (exiting) return;
  exiting = true;
  console.error(`EXIT=${code}`);
  await appendFile(liveLog, `EXIT=${code}\n`).catch(() => {});
};
process.on('exit', code => { if (!exiting) { try { appendFileSync(liveLog, `EXIT=${code}\n`); } catch {} } });
process.on('uncaughtException', async error => { await log('FAIL uncaughtException: ' + (error?.stack ?? String(error))); await watchdog('uncaught_exception'); await finish(1); process.exit(1); });
process.on('unhandledRejection', async error => { await log('FAIL unhandledRejection: ' + (error?.stack ?? String(error))); await watchdog('unhandled_rejection'); await finish(1); process.exit(1); });
for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
  process.on(signal, async () => { await log(`FAIL signal ${signal}`); await watchdog('signal_' + signal); await finish(1); process.exit(1); });
}

const flatten = entries => entries.flatMap(entry => entry.group ? flatten(entry.config ?? []) : [entry]);
const entry = (entries, name) => { const found = entries.find(item => item.name === name); assert.ok(found, 'profile missing ' + name); return found; };
const headerSnapshot = headers => Object.fromEntries([...new Headers(headers).entries()]);
const text = value => typeof value === 'string' ? value : Array.isArray(value) ? value.map(part => typeof part === 'string' ? part : part?.text ?? '').join('') : '';
const sanitize = (value, secret) => {
  if (typeof value === 'string') return secret && value.includes(secret) ? value.split(secret).join('***') : value;
  if (Array.isArray(value)) return value.map(item => sanitize(item, secret));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sanitize(item, secret)]));
  return value;
};
const writeEvidence = async (name, value, secret) => writeFile(join(evidence, name), JSON.stringify(sanitize(value, secret), null, 2));

const actors = Array.from({length: actorCount}, (_, index) => `a${String(index + 1).padStart(2, '0')}`);
const pairs = Array.from({length: actorCount / 2}, (_, index) => [actors[index * 2], actors[index * 2 + 1]]);
const momentFor = tick => `l2-tick-${tick}`;
const roleFor = (tick, index) => tick === 1 ? 'publish' : tick === 2 ? 'message' : index % 2 === 0 ? 'publish' : 'message';
const instructionFor = (role, actor, tick) => role === 'publish'
  ? `只做两步：1) read /self/profile.json（只读这一次，不要读 /context）；2) act，action 填 social.publish，input.content 写一句含 #L2 的中文帖子。不要传 target，不要调用 consumeNotifications/bash/find/grep，act 成功后立刻用一句话结束。`
  : `只做两步：1) read /self/profile.json（只读这一次，不要读 /context）；2) act，action 填 social.sendPartnerMessage，input.content 写一句给配对伙伴的中文私信。不要传 target，不要调用 consumeNotifications/bash/find/grep，act 成功后立刻用一句话结束。`;

const requests = (values['restore-from'] || values['reuse-hot']) && existsSync(requestsPath) ? JSON.parse(await readFile(requestsPath, 'utf8')) : [];
let secret;
let credentialsPath;
let lockPath;
let createdExamplesLink = false;
let originalFetch;
let currentTick = 0;
let tickRequestStart = 0;
let lastRequestAt = Date.now();
let lastTickAt = Date.now();
let lastTickDone = 0;
let errorEvents = [];
let tickRecords = [];
let failure = null;

const errorRate = () => {
  const cutoff = Date.now() - errorWindowMs;
  errorEvents = errorEvents.filter(item => item.at >= cutoff);
  const since = requests.filter(item => item.at >= cutoff);
  if (!since.length) return 0;
  return errorEvents.length / since.length;
};

const diskFreeBytes = async path => {
  try { const value = await statfs(path); return Number(value.bavail) * Number(value.bsize); }
  catch { return null; }
};

const watchdogTimer = setInterval(async () => {
  const now = Date.now();
  if (now - lastRequestAt > tickStallMs) await watchdog(`no_request_${Math.round((now - lastRequestAt) / 1000)}s`);
  else if (now - lastTickAt > tickStallMs) await watchdog(`no_tick_${Math.round((now - lastTickAt) / 1000)}s`);
  const rate = errorRate();
  if (rate > 0.2) await watchdog(`model_error_rate_${rate.toFixed(2)}`);
  const free = await diskFreeBytes('/var/tmp');
  if (free !== null && free < 5 * 1024 ** 3) await watchdog(`disk_free_${(free / 1024 ** 3).toFixed(1)}GB`);
}, 30_000);

function recordRequest(record) {
  requests.push(record);
  lastRequestAt = Date.now();
  const usage = record.usage ?? {};
  const cached = Number(usage.prompt_tokens_details?.cached_tokens ?? usage.cache_read_input_tokens ?? usage.cached_tokens ?? 0);
  const status = record.status;
  const modelError = record.error?.type ?? record.error?.code ?? (status !== null && status !== undefined && (status < 200 || status >= 300) ? `HTTP_${status}` : null);
  if (modelError) errorEvents.push({at: Date.now(), status, error: String(modelError)});
  try { appendFileSync(requestsJsonlPath, JSON.stringify(sanitize(record, secret)) + '\n'); } catch (error) { console.error('REQ_JSONL_FAIL ' + String(error?.stack ?? error)); }
  progress(`tick=${currentTick} request=${requests.length} actor=${record.actor ?? '?'} http=${status ?? 'ERR'} finish=${record.response?.finishReason ?? '?'} prompt=${usage.prompt_tokens ?? '?'} cached=${cached || '?'} ms=${record.durationMs} tools=${(record.tools ?? []).length}`);
  void persistRequests();
}

let persistQueue = Promise.resolve();
function persistRequests() {
  persistQueue = persistQueue.then(() => writeFile(requestsPath, JSON.stringify(sanitize(requests, secret), null, 2))).catch(error => { console.error('REQ_PERSIST_FAIL ' + String(error?.stack ?? error)); });
  return persistQueue;
}

async function checkpointDatabases(root) {
  const found = [];
  const walk = async dir => {
    let items;
    try { items = await (await import('node:fs/promises')).readdir(dir, {withFileTypes: true}); } catch { return; }
    for (const item of items) {
      const path = join(dir, item.name);
      if (item.isDirectory()) await walk(path);
      else if (item.name.endsWith('.sqlite')) found.push(path);
    }
  };
  await walk(root);
  for (const path of found) {
    try {
      const db = new DatabaseSync(path);
      try { db.exec('PRAGMA wal_checkpoint(TRUNCATE)'); } finally { db.close(); }
    } catch (error) {
      await log(`checkpoint skipped ${path}: ${String(error?.message ?? error)}`);
    }
  }
  return found;
}

async function mirrorTick(tick) {
  const targetBase = join(mirrorRoot, `tick-${tick}`);
  const temp = targetBase + '.tmp';
  await rm(temp, {recursive: true, force: true});
  await mkdir(temp, {recursive: true});
  await cp(join(hotRoot, 'run'), join(temp, 'run'), {recursive: true, force: true});
  await cp(join(hotRoot, 'run-lineage'), join(temp, 'run-lineage'), {recursive: true, force: true});
  await rm(targetBase, {recursive: true, force: true});
  await rename(temp, targetBase);
  const state = {tick, completedTicks: tick, latest: `tick-${tick}`, at: new Date().toISOString(), hotRoot};
  await writeFile(join(mirrorRoot, 'state.json.tmp'), JSON.stringify(state, null, 2));
  await rename(join(mirrorRoot, 'state.json.tmp'), join(mirrorRoot, 'state.json'));
  await appendFile(join(mirrorRoot, 'mirror.log'), `${new Date().toISOString()} tick=${tick} bytes=${await dirBytes(targetBase)}\n`);
  await progress(`tick=${tick} mirror=${targetBase}`);
}

async function dirBytes(dir) {
  let total = 0;
  const walk = async path => {
    let items;
    try { items = await (await import('node:fs/promises')).readdir(path, {withFileTypes: true}); } catch { return; }
    for (const item of items) {
      const next = join(path, item.name);
      if (item.isDirectory()) await walk(next);
      else { try { total += Number((await (await import('node:fs/promises')).stat(next)).size); } catch {} }
    }
  };
  await walk(dir);
  return total;
}

async function restoreFromMirror(source, target) {
  const state = JSON.parse(await readFile(join(source, 'state.json'), 'utf8'));
  const snapshot = join(source, state.latest);
  await rm(target, {recursive: true, force: true});
  await mkdir(target, {recursive: true});
  await cp(join(snapshot, 'run'), join(target, 'run'), {recursive: true, force: true});
  await cp(join(snapshot, 'run-lineage'), join(target, 'run-lineage'), {recursive: true, force: true});
  await progress(`restore tick=${state.completedTicks} from=${snapshot} to=${target}`);
  return state.completedTicks;
}

async function queryFacts() {
  const path = join(hotRoot, 'run', 'world.sqlite');
  if (!existsSync(path)) return {posts: [], messages: [], receipts: []};
  const db = new DatabaseSync(path, {readOnly: true});
  try {
    const rows = sql => db.prepare(sql).all();
    return {
      posts: rows('SELECT id,author_id,content,created_time FROM social_posts ORDER BY ordinal'),
      messages: rows('SELECT id,sender,receiver,round_no,content FROM social_messages ORDER BY ordinal'),
      receipts: rows('SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id'),
    };
  } finally { db.close(); }
}

async function queryAttempts() {
  const path = join(hotRoot, 'run-lineage', 'attempts.sqlite');
  if (!existsSync(path)) return [];
  const db = new DatabaseSync(path, {readOnly: true});
  try { return db.prepare('SELECT operation_id,status,reason FROM attempts').all(); }
  finally { db.close(); }
}

async function fetchPrefixCache() {
  const url = process.env.L2_METRICS_URL || 'http://127.0.0.1:4000/metrics';
  try {
    const response = await fetch(url, {headers: {authorization: `Bearer ${secret}`}, signal: AbortSignal.timeout(5000)});
    if (!response.ok) return {url, status: response.status, counters: null};
    const body = await response.text();
    const counters = {};
    for (const line of body.split(/\r?\n/)) {
      if (!/prefix_cache/i.test(line) || line.startsWith('#')) continue;
      const match = /^([a-zA-Z0-9_:]+)\s+([0-9.eE+-]+)$/.exec(line.trim());
      if (match) counters[match[1]] = Number(match[2]);
    }
    return {url, status: response.status, counters: Object.keys(counters).length ? counters : null};
  } catch (error) {
    return {url, status: null, counters: null, error: String(error?.message ?? error)};
  }
}

function summarizeTick(tick, started, tickRequests, factsBefore, factsAfter, attempts, prefixCache) {
  const usage = tickRequests.reduce((acc, item) => {
    const value = item.usage ?? {};
    acc.prompt += Number(value.prompt_tokens ?? 0);
    acc.completion += Number(value.completion_tokens ?? 0);
    acc.cached += Number(value.prompt_tokens_details?.cached_tokens ?? value.cache_read_input_tokens ?? value.cached_tokens ?? 0);
    return acc;
  }, {prompt: 0, completion: 0, cached: 0});
  const durations = tickRequests.map(item => item.durationMs).sort((a, b) => a - b);
  const actNames = tickRequests.flatMap(item => item.response?.toolCalls ?? []).filter(call => call.function?.name === 'act').map(call => {
    try { return JSON.parse(call.function?.arguments ?? '{}').action ?? 'invalid'; } catch { return 'invalid'; }
  });
  const byAction = actNames.reduce((acc, name) => { acc[name] = (acc[name] ?? 0) + 1; return acc; }, {});
  const expired = tickRequests.reduce((sum, item) => sum + (String(text(item.response?.content ?? '')) + '\n' + JSON.stringify(item.messages ?? [])).split('ACTION_EXPIRED').length - 1, 0);
  const receiptDelta = factsAfter.receipts.length - factsBefore.receipts.length;
  const postsDelta = factsAfter.posts.length - factsBefore.posts.length;
  const messagesDelta = factsAfter.messages.length - factsBefore.messages.length;
  const modelErrors = attempts.filter(item => /MODEL_/.test(String(item.reason ?? '')) && String(item.operation_id).includes('l2-tick-' + tick)).map(item => ({operationId: item.operation_id, status: item.status, reason: String(item.reason).slice(0, 200)}));
  return {
    tick,
    at: new Date().toISOString(),
    durationMs: Math.round(performance.now() - started),
    requests: tickRequests.length,
    promptTokens: usage.prompt,
    completionTokens: usage.completion,
    cachedTokens: usage.cached,
    cachedRatio: usage.prompt ? usage.cached / usage.prompt : null,
    latencyMs: {min: durations[0] ?? null, p50: durations[Math.floor(durations.length * 0.5)] ?? null, p95: durations[Math.floor(durations.length * 0.95)] ?? null, max: durations.at(-1) ?? null},
    byAction,
    actionExpired: expired,
    postsDelta,
    messagesDelta,
    receiptDelta,
    modelErrors,
    prefixCache,
    errorRate: errorRate(),
    maxPromptTokens: Math.max(0, ...tickRequests.map(item => Number(item.usage?.prompt_tokens ?? 0))),
    maxEstimate: Math.max(0, ...tickRequests.map(item => item.slots?.total ?? 0)),
  };
}

try {
  const env = await readFile(gatewayEnv, 'utf8');
  const line = env.split(/\r?\n/).find(item => item.startsWith('LITELLM_MASTER_KEY='));
  assert.ok(line, 'LITELLM_MASTER_KEY missing in gateway env');
  secret = line.slice('LITELLM_MASTER_KEY='.length).trim();
  assert.ok(secret.length > 0, 'LITELLM_MASTER_KEY empty');
  assert.ok(process.env.TMPDIR, 'TMPDIR required (run through tools/run.py)');
  credentialsPath = join(process.env.TMPDIR, `l2-gateway-credentials-${process.pid}.json`);
  await writeFile(credentialsPath, JSON.stringify({'local-gateway': {type: 'api_key', key: secret}}), {mode: 0o600});
  await chmod(credentialsPath, 0o600);

  let startTick = Number(values['start-tick'] ?? 1);
  if (values['restore-from']) {
    const completed = await restoreFromMirror(resolve(values['restore-from']), hotRoot);
    startTick = completed + 1;
  }
  if (values['restore-from']) {
    for (let index = requests.length - 1; index >= 0; index--) if (Number(requests[index].tick ?? 0) >= startTick) requests.splice(index, 1);
    errorEvents = [];
  }
  if (values['reuse-hot']) {
    if (requests.length) await writeFile(join(evidence, 'failed-requests-before-reuse.json'), JSON.stringify(requests, null, 2));
    requests.length = 0;
    await writeFile(requestsPath, '[]');
    await writeFile(requestsJsonlPath, '');
    await writeFile(metricsPath, '');
    await writeFile(ticksPath, '[]');
    await appendFile(liveLog, `# reuse-hot ${new Date().toISOString()} hot=${hotRoot}\n`);
  } else if (startTick === 1) await rm(hotRoot, {recursive: true, force: true});
  await mkdir(hotRoot, {recursive: true});
  if (startTick > endTick) { await log(`nothing to do start=${startTick} end=${endTick}`); await finish(0); }
  await progress(`start hot=${hotRoot} mirror=${mirrorRoot} tick=${startTick}..${endTick} actors=${actorCount} model=${modelId} transport=json capacity=20 maxRetries=2`);

  const profileJson = JSON.parse(await readFile(join(here, '..', '..', 'profiles', 'social-sqlite.json'), 'utf8'));
  const entries = flatten(profileJson.patches[0].insert);
  const {contextBudget} = await import(pathToFileURL(join(here, '..', '..', '.build', 'core', 'dist', 'contracts', 'context.js')).href);
  const budget = contextBudget();
  const baselineText = '严格按本次输入的 instruction 操作。冻结上下文路径只是引用，不要 read /context 下的路径。只允许 read /self/profile.json 一次，然后立刻 act；act 成功后不再调用任何工具。不要用正文 JSON 冒充工具调用。';

  const prompts = structuredClone(entry(entries, '@society0/core/prompts').config);
  prompts.systemSections = true;
  prompts.sections = [
    {id: 'l2-baseline', order: 0, scope: 'baseline', text: baselineText},
  ];
  const driverBase = structuredClone(entry(entries, '@society0/core/driver-llm').config);
  driverBase.maxRequests = 3;
  driverBase.maxToolCalls = 3;
  driverBase.maxTurns = 10;
  driverBase.toolChoice = 'required-first';
  driverBase.threadLifetime = 'actor';
  driverBase.perceptions = ['actor/v1', 'social/profile', 'social/notifications'];
  const modelPi = structuredClone(entry(entries, '@society0/core/model-pi').config);
  modelPi.maxTokens = 2048;
  modelPi.jsonTimeoutMs = 180000;
  modelPi.transport = {[modelId]: 'json'};
  modelPi.maxRetries = 2;
  modelPi.models = modelPi.models.map(model => ({...model, contextWindow: 16384}));
  const sqlite = structuredClone(entry(entries, '@society0/core/sqlite').config);
  Object.assign(sqlite, {directory: join(hotRoot, 'run'), runId, generation: 'g1', maxQueryRows: 100000, maxQueryBytes: 16777216, maxInlineBytes: 16777216, maxSnapshotAgeMs: 300000});
  const sqliteHistory = structuredClone(entry(entries, '@society0/core/sqlite-history').config);
  Object.assign(sqliteHistory, {directory: join(hotRoot, 'run'), runId, generation: 'g1', controlSequence: '0'});
  const attempts = structuredClone(entry(entries, '@society0/core/attempts').config);
  Object.assign(attempts, {directory: join(hotRoot, 'run-lineage'), lineage: runId});
  const activation = structuredClone(entry(entries, '@society0/core/activation').config);
  activation.capacity = 20;
  const social = structuredClone(entry(entries, '@society0/social/social').config);
  social.pairingGroups = pairs;
  social.wakeDriver = 'rule';
  const socialInit = structuredClone(entry(entries, '@society0/social/social-init').config);
  socialInit.startRound = 1;
  socialInit.pairingGroups = pairs;
  socialInit.pairingSessionMinutes = social.pairingSessionMinutes ?? 10;
  const topology = structuredClone(entry(entries, '@society0/social/topology-edges').config);
  topology.actors = actors.map((id, index) => ({id, attributes: {name: `Actor ${String(index + 1).padStart(2, '0')}`}}));
  topology.edges = pairs.flatMap(([left, right]) => [{from: right, to: left}, {from: left, to: right}]);

  const examplesModules = join(here, '..', '..', 'examples', 'node_modules');
  try { await lstat(examplesModules); }
  catch { await symlink(join('..', '.build', 'node_modules'), examplesModules, 'dir'); createdExamplesLink = true; }
  lockPath = join(process.env.TMPDIR, `l2-module-lock-${process.pid}.json`);
  const packages = {};
  for (const name of ['host', 'core', 'extras', 'social']) {
    const meta = JSON.parse(await readFile(join(here, '..', '..', '.build', name, 'generated', 'plugin.meta.json'), 'utf8'));
    packages['@society0/' + name] = meta.source;
  }
  await writeFile(lockPath, JSON.stringify({lockfile: pathToFileURL(lockPath).href, packages}));
  const {runProfile} = await import(pathToFileURL(join(here, '..', '..', 'examples', 'run-profile.mjs')).href);
  const {FileCredentialStore} = await import(pathToFileURL(join(here, '..', '..', '.build', 'core', 'dist', 'model-credentials', 'store.js')).href);

  originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = String(input?.url ?? input);
    const isModel = url.startsWith('http://127.0.0.1:4000/v1/');
    if (!isModel) return originalFetch(input, init);
    const started = performance.now();
    let body;
    try {
      const raw = init?.body ?? (input instanceof Request ? await input.clone().text() : undefined);
      body = typeof raw === 'string' ? JSON.parse(raw) : undefined;
    } catch { body = undefined; }
    const threadId = body?.metadata?.session_id ?? null;
    const actor = typeof threadId === 'string' ? (threadId.match(/thread\/actor\/([^"\]]+)/)?.[1] ?? null) : null;
    let response;
    try {
      response = await originalFetch(input, init);
    } catch (error) {
      recordRequest({seq: requests.length + 1, at: Date.now(), tick: currentTick, actor, threadId, url, durationMs: Math.round(performance.now() - started), status: null, tools: (body?.tools ?? []).map(tool => tool.function?.name ?? tool.name), toolSchemaBytes: Buffer.byteLength(JSON.stringify(body?.tools ?? [])), messages: (body?.messages ?? []).map(message => ({role: message.role, content: message.content, toolCalls: message.tool_calls})), stream: body?.stream, response: {finishReason: undefined, content: undefined, toolCalls: [], usage: {}}, usage: {}, error: {type: 'probe_transport', message: String(error?.message ?? error)}, slots: null});
      throw error;
    }
    const clone = response.clone();
    const rawResponse = await clone.text();
    const parsed = parseResponse(rawResponse);
    const usage = parsed?.usage ?? {};
    const record = {
      seq: requests.length + 1,
      at: Date.now(),
      tick: currentTick,
      actor,
      threadId,
      url,
      durationMs: Math.round(performance.now() - started),
      status: response.status,
      tools: (body?.tools ?? []).map(tool => tool.function?.name ?? tool.name),
      toolSchemaBytes: Buffer.byteLength(JSON.stringify(body?.tools ?? [])),
      messages: (body?.messages ?? []).map(message => ({role: message.role, content: message.content, toolCalls: message.tool_calls})),
      stream: body?.stream,
      response: {finishReason: parsed?.finishReason, content: parsed?.content, toolCalls: parsed?.toolCalls ?? [], usage, id: parsed?.id},
      usage,
      error: response.ok ? null : sanitizeError(parsed, response.status),
      cacheControl: {retryAfter: response.headers.get('retry-after') ?? null, litellm: Object.fromEntries([...response.headers.entries()].filter(([name]) => /^x-litellm-/i.test(name)))},
    };
    record.slots = estimateSlots(record, budget, baselineText);
    recordRequest(record);
    return response;
  };

  const allTicks = (values['restore-from'] && existsSync(ticksPath) ? JSON.parse(await readFile(ticksPath, 'utf8')) : []).filter(item => Number(item.tick ?? 0) < startTick);
  for (let tick = startTick; tick <= endTick; tick++) {
    currentTick = tick;
    tickRequestStart = requests.length;
    lastTickAt = Date.now();
    await progress(`TICK_START tick=${tick} requests_so_far=${requests.length} hot=${hotRoot}`);
    const factsBefore = await queryFacts();
    const prefixBefore = await fetchPrefixCache();
    const started = performance.now();
    let report;
    let lastError;
    for (let attempt = 1; attempt <= 5; attempt++) {
      const proposals = Array.from({length: tick}, (_, index) => index === tick - 1 ? {
        id: `l2-tick-${tick}`,
        planKind: 'staged/1',
        payload: {
          nextCursor: String(tick),
          phases: [{
            id: 'turn',
            activations: actors.map((actorId, index) => {
              const role = roleFor(tick, index);
              return {
                id: `l2-tick-${tick}/activation/${actorId}`,
                actorId,
                momentId: momentFor(tick),
                source: 'l2-pilot',
                driver: 'llm/v1',
                input: {tick: String(tick), actor: actorId, role, instruction: instructionFor(role, actorId, tick)},
              };
            }),
          }],
        },
      } : {id: `placeholder-${index}`, planKind: 'staged/1', payload: {nextCursor: String(index + 1), phases: []}});
      const driver = {...driverBase, requiredActions: []};
      const patchesPath = join(process.env.TMPDIR, `l2-patches-${process.pid}-${tick}-${attempt}.json`);
      await writeFile(patchesPath, JSON.stringify([
        {id: 'sqlite', config: sqlite},
        {id: 'sqlite-history', config: sqliteHistory},
        {id: 'attempts', config: attempts},
        {id: 'activation', config: activation},
        {id: 'topology-edges', config: topology},
        {id: 'social', config: social},
        {id: 'social-init', config: socialInit},
        {id: 'prompts', config: prompts},
        {id: 'driver-llm', config: driver},
        {id: 'model-pi', config: modelPi},
        {id: 'round-robin', disabled: true},
        {id: 'generation', insert: [{id: 'l2-proposals', name: '@society0/core/proposal-sequence', config: {proposals}}]},
      ], null, 2));
      try {
        report = await runProfile({
          profile: join(here, '..', '..', 'profiles', 'social-sqlite.json'),
          directory: join(hotRoot, 'run'),
          lineageDirectory: join(hotRoot, 'run-lineage'),
          runId,
          patches: patchesPath,
          lock: lockPath,
          allowModelRequests: true,
          reopen: tick > 1 || values['reuse-hot'],
          configureHost: async ctx => { await ctx.modelCredentials.register('authorized-local', new FileCredentialStore(credentialsPath)); },
        });
        lastError = null;
        await rm(patchesPath, {force: true}).catch(() => {});
        break;
      } catch (error) {
        lastError = error;
        await log(`FAIL tick=${tick} attempt=${attempt}: ${String(error?.stack ?? error).split('\n').slice(0, 6).join(' | ')}`);
        await rm(patchesPath, {force: true}).catch(() => {});
        const failed = requests.slice(tickRequestStart);
        if (failed.length) await writeFile(join(evidence, `failed-requests-tick-${tick}-attempt-${attempt}.json`), JSON.stringify(failed, null, 2));
        requests.length = tickRequestStart;
        await persistRequests();
        await new Promise(resolve => setTimeout(resolve, 10000 * attempt));
        if (tick > 1) await restoreFromMirror(mirrorRoot, hotRoot);
        else if (!values['reuse-hot']) await rm(hotRoot, {recursive: true, force: true});
      }
    }
    if (!report) {
      await watchdog(`tick_${tick}_failed`);
      await finish(1);
      failure = lastError;
      break;
    }
    if (report.result?.status !== 'completed') {
      await watchdog(`tick_${tick}_incomplete_${report.result?.status}`);
    }
    const factsAfter = await queryFacts();
    const prefixAfter = await fetchPrefixCache();
    const prefixDelta = prefixDeltaOf(prefixBefore, prefixAfter);
    const attemptsRows = await queryAttempts();
    const tickRequests = requests.slice(tickRequestStart);
    const tickRecord = summarizeTick(tick, started, tickRequests, factsBefore, factsAfter, attemptsRows, prefixDelta);
    tickRecord.activations = report.result?.evidence?.activations?.map(item => ({status: item.status, reason: item.reason, actor: item.actorId})) ?? [];
    tickRecord.status = report.result?.status;
    allTicks.push(tickRecord);
    await writeFile(ticksPath, JSON.stringify(sanitize(allTicks, secret), null, 2));
    await appendFile(metricsPath, JSON.stringify(sanitize(tickRecord, secret)) + '\n');
    await checkpointDatabases(hotRoot);
    await mirrorTick(tick);
    lastTickAt = Date.now();
    lastTickDone = tick;
    const toolFailures = tickRecord.activations.filter(item => item.reason === 'tool_failed').length;
    await progress(`TICK_DONE tick=${tick} status=${tickRecord.status} requests=${tickRecord.requests} prompt=${tickRecord.promptTokens} cached=${tickRecord.cachedTokens} posts+=${tickRecord.postsDelta} messages+=${tickRecord.messagesDelta} tool_failed=${toolFailures} actions=${JSON.stringify(tickRecord.byAction)}`);
    if (report.result?.status !== 'completed') {
      await log(`tick ${tick} incomplete; stopping for repair instead of faking completion`);
      break;
    }
  }

  if (lastTickDone < endTick && !failure) failure = Error(`INCOMPLETE_TICKS ${lastTickDone}/${endTick}`);
  const summary = {
    at: new Date().toISOString(),
    model: modelId,
    transport: 'json',
    actorCount,
    ticks: allTicks,
    totalRequests: requests.length,
    hotRoot,
    mirrorRoot,
    completedTicks: lastTickDone,
    startedTick: startTick,
    errorRate: errorRate(),
    metricsFile: metricsPath,
  };
  await writeEvidence('summary.json', summary, secret);
  await persistRequests();
  await progress(`DONE completed=${lastTickDone}/${endTick} requests=${requests.length} errorRate=${errorRate().toFixed(3)}`);
  await finish(failure ? 1 : 0);
  if (failure) process.exitCode = 1;
} catch (error) {
  failure = error;
  await log('FAIL ' + (error?.stack ?? String(error)));
  try { await writeEvidence('failure.json', {error: String(error?.message ?? error), requests}, secret); } catch {}
  await watchdog('fatal_error');
  await finish(1);
  process.exitCode = 1;
} finally {
  clearInterval(watchdogTimer);
  if (originalFetch) globalThis.fetch = originalFetch;
  if (credentialsPath) await rm(credentialsPath, {force: true}).catch(() => {});
  if (lockPath) await rm(lockPath, {force: true}).catch(() => {});
  if (createdExamplesLink) await unlink(join(here, '..', '..', 'examples', 'node_modules')).catch(() => {});
}

function parseResponse(raw) {
  try {
    const body = JSON.parse(raw);
    const choice = body?.choices?.[0];
    return {id: body?.id, finishReason: choice?.finish_reason, content: choice?.message?.content, toolCalls: choice?.message?.tool_calls ?? [], usage: body?.usage ?? {}, error: body?.error};
  } catch {}
  const calls = new Map();
  let id, finishReason, content = '', usage = {};
  for (const line of raw.split(/\r?\n/)) {
    if (!line.startsWith('data: ')) continue;
    const data = line.slice(6).trim();
    if (!data || data === '[DONE]') continue;
    let body;
    try { body = JSON.parse(data); } catch { continue; }
    id = body.id ?? id;
    usage = body.usage ?? usage;
    const choice = body.choices?.[0];
    if (!choice) continue;
    finishReason = choice.finish_reason ?? finishReason;
    const delta = choice.delta ?? {};
    if (typeof delta.content === 'string') content += delta.content;
    for (const [index, part] of (delta.tool_calls ?? []).entries()) {
      const key = part.index ?? index;
      const call = calls.get(key) ?? {id: part.id, type: part.type ?? 'function', function: {name: '', arguments: ''}};
      if (part.id) call.id = part.id;
      if (part.function?.name) call.function.name += part.function.name;
      if (part.function?.arguments) call.function.arguments += part.function.arguments;
      calls.set(key, call);
    }
  }
  return {id, finishReason, content: content || undefined, toolCalls: [...calls.values()], usage, error: undefined};
}

function sanitizeError(parsed, status) {
  const item = parsed?.error ?? parsed;
  return {type: item?.type ?? null, code: item?.code ?? String(status), message: typeof item?.message === 'string' ? item.message.slice(0, 500) : null};
}

function prefixDeltaOf(before, after) {
  let counters = null;
  if (before?.counters && after?.counters) {
    counters = {};
    for (const key of Object.keys(after.counters)) {
      if (!key.includes('prefix_cache')) continue;
      counters[key] = {before: before.counters[key] ?? 0, after: after.counters[key], delta: (after.counters[key] ?? 0) - (before.counters[key] ?? 0)};
    }
    if (!Object.keys(counters).length) counters = null;
  }
  return {before: before?.counters ?? null, after: after?.counters ?? null, delta: counters, status: after?.status ?? null};
}
