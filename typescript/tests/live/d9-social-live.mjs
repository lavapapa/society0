#!/usr/bin/env node
/**
 * D9 真实端到端验收：直接装配 profiles/social-sqlite.json，走本地 LiteLLM 网关。
 * 手动运行：node tests/live/d9-social-live.mjs
 * 只打两类补丁：真实模型凭据（临时 0600 文件）与运行目录；场景 prompt/topology 由脚本给出。
 */
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, chmod, rm, appendFile, symlink, lstat, unlink } from 'node:fs/promises';
import { appendFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';

const here = dirname(fileURLToPath(import.meta.url));
const W = join(here, '..', '..', '..', '..', '..');
const evidence = join(W, 'evidence', 'd9');
const gatewayEnv = process.env.D9_GATEWAY_ENV;
if (!gatewayEnv) { console.error('D9_GATEWAY_ENV must point to the local LiteLLM gateway .env file'); process.exit(2); }
const modelId = 'Qwen3.6-35B-A3B-FP8';
const maxRequests = 30;
const watchdogMs = 15 * 60 * 1000;
const runDirectory = `/var/tmp/society0-next-d9-${new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)}`;

await mkdir(evidence, { recursive: true });
const liveLog = join(evidence, 'live.log');
await writeFile(liveLog, '');
const log = async message => { await appendFile(liveLog, message + '\n').catch(() => {}); console.error(message); };
const sanitize = (value, secret) => {
  if (typeof value === 'string') return secret && value.includes(secret) ? value.split(secret).join('***') : value;
  if (Array.isArray(value)) return value.map(item => sanitize(item, secret));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sanitize(item, secret)]));
  return value;
};
const writeEvidence = async (name, value, secret) => writeFile(join(evidence, name), JSON.stringify(sanitize(value, secret), null, 2));
let exiting = false;
const finish = async code => {
  if (exiting) return;
  exiting = true;
  await appendFile(liveLog, `EXIT=${code}\n`).catch(() => {});
};
process.on('exit', code => { if (!exiting) { try { appendFileSync(liveLog, `EXIT=${code}\n`); } catch {} } });
process.on('uncaughtException', async error => { await log('FAIL uncaughtException: ' + (error?.stack ?? String(error))); await finish(1); process.exit(1); });
process.on('unhandledRejection', async error => { await log('FAIL unhandledRejection: ' + (error?.stack ?? String(error))); await finish(1); process.exit(1); });
const watchdog = setTimeout(async () => { await log(`FAIL watchdog: exceeded ${watchdogMs}ms`); await finish(124); process.exit(124); }, watchdogMs);

const requests = [];
const usageTotals = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
let secret;
let credentialsPath;
let patchesPath;
let lockPath;
let createdExamplesLink = false;
let originalFetch;
let profileJson;

const flatten = entries => entries.flatMap(entry => entry.group ? flatten(entry.config ?? []) : [entry]);
const entry = (entries, name) => { const found = entries.find(item => item.name === name); assert.ok(found, 'profile missing ' + name); return found; };
const toolNames = body => (body.tools ?? []).map(tool => tool.function?.name ?? tool.name);
const headerSnapshot = headers => Object.fromEntries([...new Headers(headers).entries()]);

try {
  const env = await readFile(gatewayEnv, 'utf8');
  const line = env.split(/\r?\n/).find(item => item.startsWith('LITELLM_MASTER_KEY='));
  assert.ok(line, 'LITELLM_MASTER_KEY missing in gateway env');
  secret = line.slice('LITELLM_MASTER_KEY='.length).trim();
  assert.ok(secret.length > 0, 'LITELLM_MASTER_KEY empty');
  assert.ok(process.env.TMPDIR, 'TMPDIR required');
  credentialsPath = join(process.env.TMPDIR, `d9-social-gateway-credentials-${process.pid}.json`);
  await writeFile(credentialsPath, JSON.stringify({ 'local-gateway': { type: 'api_key', key: secret } }), { mode: 0o600 });
  await chmod(credentialsPath, 0o600);

  profileJson = JSON.parse(await readFile(join(here, '..', '..', 'profiles', 'social-sqlite.json'), 'utf8'));
  const entries = flatten(profileJson.patches[0].insert);

  // 场景：2 个 moment（round/0、round/1），alice 每轮发布并给配对伙伴发私信。
  const prompts = structuredClone(entry(entries, '@society0/core/prompts').config);
  prompts.systemSections = true;
  prompts.sections = [{
    id: 'd9-live',
    order: 0,
    scope: 'baseline',
    text: [
      '只看本次输入里的 round 字段，并且只做一件事。',
      'round 为 0：调用 act({"action":"social.publish","input":{"content":"<一句中文帖子，包含 #D9Live>"}})。',
      'round 为 1：调用 act({"action":"social.sendPartnerMessage","input":{"content":"<一句给配对伙伴的中文私信>"}})。',
      'action 只填 social.publish 或 social.sendPartnerMessage；不要传 target，不要把 act 填进 action。禁止调用 bash、social.broadcastToGroup、social.consumeNotifications 或其他任何行动；不要用正文输出 JSON 代替工具调用。完成后用不超过 30 字的一句话回复。',
    ].join('\n'),
  }];
  const round = structuredClone(entry(entries, '@society0/social/round-robin').config);
  round.actors = ['alice'];
  round.rounds = 2;
  const driver = structuredClone(entry(entries, '@society0/core/driver-llm').config);
  driver.maxRequests = 4;
  driver.maxToolCalls = 4;
  driver.toolChoice = 'required-first';
  const modelPi = structuredClone(entry(entries, '@society0/core/model-pi').config);
  modelPi.maxTokens = 2048;
  modelPi.jsonTimeoutMs = 180000;
  modelPi.transport = { [modelId]: 'json' };
  modelPi.models = modelPi.models.map(model => ({ ...model, maxTokens: 2048, reasoning: false }));
  driver.requiredActions = [];
  const social = structuredClone(entry(entries, '@society0/social/social').config);
  social.wakeDriver = 'rule';
  const socialInit = structuredClone(entry(entries, '@society0/social/social-init').config);
  socialInit.startRound = 1;
  socialInit.pairingGroups = structuredClone(social.pairingGroups);
  socialInit.pairingSessionMinutes = social.pairingSessionMinutes;
  const sqlite = structuredClone(entry(entries, '@society0/core/sqlite').config);
  Object.assign(sqlite, {
    directory: runDirectory, runId: 'd9', generation: 'g1',
    maxQueryRows: 100000, maxQueryBytes: 16777216, maxInlineBytes: 16777216, maxSnapshotAgeMs: 300000,
  });
  patchesPath = join(process.env.TMPDIR, `d9-social-patches-${process.pid}.json`);
  await writeFile(patchesPath, JSON.stringify([
    { id: 'sqlite', config: sqlite },
    { id: 'social', config: social },
    { id: 'social-init', config: socialInit },
    { id: 'prompts', config: prompts },
    { id: 'round-robin', config: round },
    { id: 'driver-llm', config: driver },
    { id: 'model-pi', config: modelPi },
    { id: 'generation', insert: [
      { id: 'driver-rule', name: '@society0/core/driver-rule', config: { handler: 'rule/passive/v1', sources: ['actor/v1'] } },
      { id: 'rule-passive', name: '@society0/core/rule-passive', config: {} },
    ] },
  ], null, 2));

  // 请求/响应证据：只记录 4000 的模型请求，key 一律替换为 ***。
  originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = String(input?.url ?? input);
    const isModel = url.startsWith('http://127.0.0.1:4000/v1/');
    const started = performance.now();
    const response = await originalFetch(input, init);
    if (!isModel) return response;
    const index = requests.length;
    let body;
    try {
      const raw = init?.body ?? (input instanceof Request ? await input.clone().text() : undefined);
      body = typeof raw === 'string' ? JSON.parse(raw) : undefined;
    } catch { body = undefined; }
    const headers = headerSnapshot(init?.headers ?? input?.headers ?? {});
    const clone = response.clone();
    let parsed;
    try { parsed = JSON.parse(await clone.text()); } catch { parsed = undefined; }
    const usage = parsed?.usage ?? {};
    for (const key of Object.keys(usageTotals)) usageTotals[key] += Number(usage[key] ?? 0);
    const record = {
      index,
      url,
      method: init?.method ?? 'POST',
      durationMs: Math.round(performance.now() - started),
      headers,
      model: body?.model,
      stream: body?.stream,
      toolChoice: body?.tool_choice,
      tools: toolNames(body ?? {}),
      system: (body?.messages ?? []).filter(message => message.role === 'system').map(message => message.content).slice(0, 1),
      messages: (body?.messages ?? []).map(message => ({ role: message.role, content: message.content, toolCalls: message.tool_calls })),
      response: {
        finishReason: parsed?.choices?.[0]?.finish_reason,
        toolCalls: parsed?.choices?.[0]?.message?.tool_calls ?? [],
        content: parsed?.choices?.[0]?.message?.content,
        reasoning: parsed?.choices?.[0]?.message?.reasoning_content,
        usage,
        id: parsed?.id,
      },
    };
    assert.ok(record.tools.length === 5, 'request must expose exactly 5 tools: ' + JSON.stringify(record.tools));
    for (const name of ['bash', 'read', 'find', 'grep', 'act']) assert.ok(record.tools.includes(name), 'missing tool ' + name);
    requests.push(record);
    return response;
  };

  // 开发树没有安装态 node_modules：给 examples 一个临时链接，让 run-profile 的包解析与 .build 一致；运行结束删除。
  const examplesModules = join(here, '..', '..', 'examples', 'node_modules');
  try { await lstat(examplesModules); }
  catch { await symlink(join('..', '.build', 'node_modules'), examplesModules, 'dir'); createdExamplesLink = true; }
  lockPath = join(process.env.TMPDIR, `d9-social-module-lock-${process.pid}.json`);
  const packages = {};
  for (const name of ['host', 'core', 'extras', 'social']) {
    const meta = JSON.parse(await readFile(join(here, '..', '..', '.build', name, 'generated', 'plugin.meta.json'), 'utf8'));
    packages['@society0/' + name] = meta.source;
  }
  await writeFile(lockPath, JSON.stringify({ lockfile: pathToFileURL(lockPath).href, packages }));
  const { runProfile } = await import(pathToFileURL(join(here, '..', '..', 'examples', 'run-profile.mjs')).href);
  const { FileCredentialStore } = await import(pathToFileURL(join(here, '..', '..', '.build', 'core', 'dist', 'model-credentials', 'store.js')).href);
  let afterRunEvidence;
  const report = await runProfile({
    profile: join(here, '..', '..', 'profiles', 'social-sqlite.json'),
    directory: runDirectory,
    lineageDirectory: runDirectory + '-lineage',
    runId: 'd9',
    patches: patchesPath,
    lock: lockPath,
    allowModelRequests: true,
    configureHost: async ctx => {
      await log('configureHost entered');
      // 只补凭据：临时凭据文件 + 现有 model-credentials 服务。
      await ctx.modelCredentials.register('authorized-local', new FileCredentialStore(credentialsPath));
      await log('credential store registered');
    },
    afterRun: async ({ ctx, cut, tree }) => {
      await log('afterRun entered');
      const generation = tree.entries.get('generation').ctx;
      const posts = (await ctx.observation.query({ sql: 'SELECT id,author_id,content,created_time,kind FROM social_posts ORDER BY ordinal' })).rows;
      const messages = (await ctx.observation.query({ sql: 'SELECT id,sender,receiver,round_no,content FROM social_messages ORDER BY ordinal' })).rows;
      const notifications = (await ctx.observation.query({ sql: 'SELECT id,actor,kind,data FROM social_notifications ORDER BY ordinal' })).rows;
      const receipts = (await ctx.observation.query({ sql: 'SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id' })).rows;

      let publicMatches = [];
      let publicError;
      try {
        const reservation = await ctx.get('executionFence').reserve({ id: 'd9-public' });
        try {
          const lease = await generation.get('exploration').acquire({ actorId: 'alice', momentId: 'round/1', query: null }, {
            runId: ctx.database.runId, generation: 'g1', operationId: 'd9-public', signal: reservation.signal,
            writeLease: reservation.lease, database: ctx.database, actorId: 'alice', momentId: 'round/1',
          });
          try {
            const found = await lease.value.withView(current => current.grep({ root: '/public', pattern: '#D9Live', mode: 'literal', page: { maxItems: 100, maxResponseBytes: 262144 } }));
            publicMatches = found.items;
          } finally { await lease.release(); }
        } finally { await reservation.release(); }
      } catch (error) { publicError = String(error?.message ?? error); }

      afterRunEvidence = { cut, posts, messages, notifications, receipts, publicMatches, publicError };
      return afterRunEvidence;
    },
  });

  const published = afterRunEvidence.posts.filter(row => row.author_id === 'alice');
  const messages = afterRunEvidence.messages;
  assert.ok(published.length >= 1, 'model must publish via act: ' + JSON.stringify(afterRunEvidence.posts));
  assert.ok(messages.length >= 1, 'model must send a partner message via act');
  assert.ok(messages.some(row => row.receiver === 'bob'), 'partner message must reach bob');
  assert.ok(afterRunEvidence.publicMatches.length >= 1, 'public /public/social/posts must expose the new post: ' + JSON.stringify({ publicError: afterRunEvidence.publicError, matches: afterRunEvidence.publicMatches }));
  assert.ok(requests.length <= maxRequests, `model requests ${requests.length} exceed ${maxRequests}`);
  assert.ok(requests.every(item => item.stream === false), 'transport must be json (stream=false)');

  await writeEvidence('requests.json', requests.map(item => ({ ...item, headers: Object.fromEntries(Object.entries(item.headers).map(([key, value]) => [key, key.toLowerCase() === 'authorization' ? '***' : value])) })), secret);
  await writeFile(join(evidence, 'system-first.txt'), (requests[0]?.system?.[0] ?? '') + '\n');
  await writeEvidence('tool-calls.json', requests.map(item => ({ index: item.index, toolCalls: item.response.toolCalls })), secret);
  await writeEvidence('world.json', {
    cut: afterRunEvidence.cut,
    posts: afterRunEvidence.posts,
    messages: afterRunEvidence.messages,
    notifications: afterRunEvidence.notifications,
    actionReceipts: afterRunEvidence.receipts,
    publicMatches: afterRunEvidence.publicMatches,
    publicError: afterRunEvidence.publicError,
  }, secret);
  const summary = {
    at: new Date().toISOString(),
    model: modelId,
    baseUrl: 'http://127.0.0.1:4000/v1',
    transport: 'json',
    runDirectory,
    status: report?.result?.status,
    modelRequests: requests.length,
    toolsPerRequest: requests.map(item => item.tools),
    requestDurationsMs: requests.map(item => item.durationMs),
    totalDurationMs: requests.reduce((sum, item) => sum + item.durationMs, 0),
    usage: usageTotals,
    posts: afterRunEvidence.posts.length,
    messages: afterRunEvidence.messages.length,
    notifications: afterRunEvidence.notifications.length,
    publicMatches: afterRunEvidence.publicMatches.length,
    systemFirst: requests[0]?.system?.[0] ?? '',
    toolCalls: requests.flatMap(item => item.response.toolCalls),
  };
  await writeEvidence('summary.json', summary, secret);
  await writeEvidence('report.json', { result: report?.result, metrics: report?.metrics, source: report?.source }, secret);
  await log(`OK status=${summary.status} requests=${summary.modelRequests} posts=${summary.posts} messages=${summary.messages} publicMatches=${summary.publicMatches} usage=${JSON.stringify(usageTotals)}`);
  await finish(0);
} catch (error) {
  await log('FAIL ' + (error?.stack ?? String(error)));
  try { await writeEvidence('failure.json', { error: String(error?.message ?? error), requests }, secret); } catch {}
  await finish(1);
  process.exitCode = 1;
} finally {
  clearTimeout(watchdog);
  if (originalFetch) globalThis.fetch = originalFetch;
  if (credentialsPath) await rm(credentialsPath, { force: true }).catch(() => {});
  if (patchesPath) await rm(patchesPath, { force: true }).catch(() => {});
  if (lockPath) await rm(lockPath, { force: true }).catch(() => {});
  if (createdExamplesLink) await unlink(join(here, '..', '..', 'examples', 'node_modules')).catch(() => {});
}
