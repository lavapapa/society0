#!/usr/bin/env node
/**
 * L1 小探针：真实模型下测 5 工具 + system + 8k 预算各槽。
 * 手动运行：node tests/live/l1-probe.mjs
 */
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, chmod, rm, appendFile, symlink, lstat, unlink } from 'node:fs/promises';
import { appendFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';

const here = dirname(fileURLToPath(import.meta.url));
const W = join(here, '..', '..', '..', '..', '..');
const evidence = join(W, 'evidence', 'l1');
const gatewayEnv = process.env.L1_GATEWAY_ENV || join(W, '..', '..', 'services', 'litellm', '.env');
const modelId = 'Qwen3.6-35B-A3B-FP8';
const maxCalls = 20;
const mainCalls = 10;
const watchdogMs = 15 * 60 * 1000;
const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
const runRoot = `/var/tmp/society0-next-l1-${stamp}`;

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
let secret;
let credentialsPath;
let patchesPath;
let lockPath;
let createdExamplesLink = false;
let originalFetch;
let contextBudget;

const flatten = entries => entries.flatMap(entry => entry.group ? flatten(entry.config ?? []) : [entry]);
const entry = (entries, name) => { const found = entries.find(item => item.name === name); assert.ok(found, 'profile missing ' + name); return found; };
const headerSnapshot = headers => Object.fromEntries([...new Headers(headers).entries()]);
const text = value => typeof value === 'string' ? value : Array.isArray(value) ? value.map(part => typeof part === 'string' ? part : part?.text ?? '').join('') : '';

function sectionsOf(content) {
  const lines = String(content ?? '').split('\n');
  const find = labels => {
    const index = lines.findIndex(line => labels.some(label => line.startsWith(label)));
    if (index < 0) return '';
    return lines.slice(index).join('\n');
  };
  return {
    state: find(['当前状态', '状态（变更）']),
    actions: find(['可用行动', '可用行动（变更）']),
    percepts: find(['感知', '感知（变更）']),
  };
}

function parseResponse(raw) {
  try { const body = JSON.parse(raw); const message = body?.choices?.[0]?.message ?? {}; return { id: body?.id, finishReason: body?.choices?.[0]?.finish_reason, content: message.content, toolCalls: message.tool_calls ?? [], usage: body?.usage ?? {} }; } catch {}
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
      const call = calls.get(key) ?? { id: part.id, type: part.type ?? 'function', function: { name: '', arguments: '' } };
      if (part.id) call.id = part.id;
      if (part.function?.name) call.function.name += part.function.name;
      if (part.function?.arguments) call.function.arguments += part.function.arguments;
      calls.set(key, call);
    }
  }
  return { id, finishReason, content: content || undefined, toolCalls: [...calls.values()], usage };
}
function toolCallLegal(call) {
  try {
    const args = JSON.parse(call.function?.arguments ?? '');
    const name = call.function?.name;
    if (name === 'read') return typeof args.path === 'string';
    if (name === 'find') return typeof args.root === 'string';
    if (name === 'grep') return typeof args.root === 'string' && typeof args.pattern === 'string';
    if (name === 'bash') return typeof args.command === 'string';
    if (name === 'act') return typeof args.action === 'string' && /^social\.[A-Za-z]+$/.test(args.action) && args.target === undefined && (args.action !== 'social.publish' || typeof args.input?.content === 'string');
    return false;
  } catch { return false; }
}
function slotEstimate(record, budget, baselineText) {
  const messages = record.messages ?? [];
  const system = messages.find(message => message.role === 'system');
  const current = [...messages].reverse().find(message => message.role === 'user' && String(message.content).includes('本次输入'));
  const perceptMessages = messages.filter(message => message.role === 'user' && String(message.content).trim().startsWith('{"source"'));
  const inputSections = sectionsOf(current?.content);
  const header = budget.estimate(text(system?.content)) + Math.ceil(Number(record.toolSchemaBytes ?? 0) / 3.2);
  const baseline = budget.estimate(baselineText);
  const stateDelta = budget.estimate(inputSections.state);
  const actions = budget.estimate(inputSections.actions);
  const percepts = perceptMessages.reduce((sum, message) => sum + budget.estimate(text(message.content)), 0);
  const total = messages.reduce((sum, message) => sum + budget.estimate(message.content), 0) + (system ? 0 : 0);
  const output = Number(record.response?.usage?.completion_tokens ?? 0) || budget.estimate(text(record.response?.content));
  const history = Math.max(0, total - header - baseline - stateDelta - actions - percepts - output);
  return { header, baseline, stateDelta, actions, percepts, history, output, total, promptTokens: Number(record.response?.usage?.prompt_tokens ?? 0) || null, budget: budget.tokens };
}

const modes = ['read', 'find', 'grep', 'act', 'read', 'find', 'grep', 'act', 'read', 'find'];
const runSpecs = [
  ...Array.from({ length: 10 }, (_, index) => ({
    name: `m${Math.floor(index / 2) + 1}-${index % 2 ? 'bob' : 'alice'}`,
    actorId: index % 2 ? 'bob' : 'alice',
    momentId: `l1-m${Math.floor(index / 2) + 1}`,
    mode: modes[index],
    maxRequests: 1,
  })),
  { name: 'json-d1', actorId: 'alice', momentId: 'l1-json', mode: 'act', maxRequests: 1, explicitJson: true },
];
const instructionFor = mode => mode === 'read' ? '只调用 read 工具，读取 /self/profile.json，不要 act。'
  : mode === 'find' ? '只调用 find 工具，列出 /public，不要 act。'
  : mode === 'grep' ? '只调用 grep 工具，在 /public 搜索 #L1，不要 act。'
  : '只调用 act social.publish，发布含 #L1 的帖子，不要传 target。';


try {
  const env = await readFile(gatewayEnv, 'utf8');
  const line = env.split(/\r?\n/).find(item => item.startsWith('LITELLM_MASTER_KEY='));
  assert.ok(line, 'LITELLM_MASTER_KEY missing in gateway env');
  secret = line.slice('LITELLM_MASTER_KEY='.length).trim();
  assert.ok(secret.length > 0, 'LITELLM_MASTER_KEY empty');
  assert.ok(process.env.TMPDIR, 'TMPDIR required (run through tools/run.py)');
  credentialsPath = join(process.env.TMPDIR, `l1-gateway-credentials-${process.pid}.json`);
  await writeFile(credentialsPath, JSON.stringify({ 'local-gateway': { type: 'api_key', key: secret } }), { mode: 0o600 });
  await chmod(credentialsPath, 0o600);

  const profileJson = JSON.parse(await readFile(join(here, '..', '..', 'profiles', 'social-sqlite.json'), 'utf8'));
  const entries = flatten(profileJson.patches[0].insert);
  contextBudget = (await import(pathToFileURL(join(here, '..', '..', '.build', 'core', 'dist', 'contracts', 'context.js')).href)).contextBudget;
  const budget = contextBudget();
  const baselineText = 'L1-BASELINE 按本次输入的 instruction 选择工具；每次回复必须调用一个工具，不要解释。不要用正文 JSON 冒充工具调用。';
  const headerText = 'L1-HEADER 工具使用守则：先读后 act；不要输出伪造的工具调用。';
  const prompts = structuredClone(entry(entries, '@society0/core/prompts').config);
  prompts.systemSections = true;
  prompts.sections = [
    { id: 'l1-header', order: 0, scope: 'header', text: headerText },
    { id: 'l1-baseline', order: 1, scope: 'baseline', text: baselineText },
  ];
  const driver = structuredClone(entry(entries, '@society0/core/driver-llm').config);
  driver.requiredActions = [];
  driver.toolChoice = 'required-first';
  driver.maxToolCalls = 10;
  driver.maxTurns = 10;
  const modelPi = structuredClone(entry(entries, '@society0/core/model-pi').config);
  modelPi.jsonTimeoutMs = 180000;
  modelPi.transport = { [modelId]: 'sse' };
  const sqlite = structuredClone(entry(entries, '@society0/core/sqlite').config);
  sqlite.maxQueryRows = 100000;
  sqlite.maxQueryBytes = 16777216;
  sqlite.maxInlineBytes = 16777216;
  sqlite.maxSnapshotAgeMs = 300000;

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
    const rawResponse = await clone.text();
    const parsed = parseResponse(rawResponse);
    const toolCalls = parsed?.toolCalls ?? [];
    const record = {
      index,
      url,
      method: init?.method ?? 'POST',
      durationMs: Math.round(performance.now() - started),
      headers,
      model: body?.model,
      stream: body?.stream,
      toolChoice: body?.tool_choice,
      tools: (body?.tools ?? []).map(tool => tool.function?.name ?? tool.name),
      toolSchemaBytes: Buffer.byteLength(JSON.stringify(body?.tools ?? [])),
      system: (body?.messages ?? []).filter(message => message.role === 'system').map(message => message.content).slice(0, 1),
      messages: (body?.messages ?? []).map(message => ({ role: message.role, content: message.content, toolCalls: message.tool_calls })),
      response: {
        finishReason: parsed?.finishReason,
        toolCalls,
        content: parsed?.content,
        usage: parsed?.usage ?? {},
        id: parsed?.id,
      },
    };
    assert.equal(record.tools.length, 5, 'request must expose exactly 5 tools: ' + JSON.stringify(record.tools));
    for (const name of ['bash', 'read', 'find', 'grep', 'act']) assert.ok(record.tools.includes(name), 'missing tool ' + name);
    record.slots = slotEstimate(record, budget, baselineText);
    record.toolCallLegal = toolCalls.map(toolCallLegal);
    record.pseudoToolCall = /\{\s*"action"\s*:|<tool_call>/i.test(text(parsed?.content)) && toolCalls.length === 0;
    requests.push(record);
    await log(`request ${index + 1} ${record.durationMs}ms tools=${record.tools.join(',')} finish=${record.response.finishReason} toolCalls=${toolCalls.length} prompt=${record.response.usage.prompt_tokens ?? '?'}`);
    return response;
  };

  const examplesModules = join(here, '..', '..', 'examples', 'node_modules');
  try { await lstat(examplesModules); }
  catch { await symlink(join('..', '.build', 'node_modules'), examplesModules, 'dir'); createdExamplesLink = true; }
  lockPath = join(process.env.TMPDIR, `l1-module-lock-${process.pid}.json`);
  const packages = {};
  for (const name of ['host', 'core', 'extras', 'social']) {
    const meta = JSON.parse(await readFile(join(here, '..', '..', '.build', name, 'generated', 'plugin.meta.json'), 'utf8'));
    packages['@society0/' + name] = meta.source;
  }
  await writeFile(lockPath, JSON.stringify({ lockfile: pathToFileURL(lockPath).href, packages }));
  const { runProfile } = await import(pathToFileURL(join(here, '..', '..', 'examples', 'run-profile.mjs')).href);
  const { FileCredentialStore } = await import(pathToFileURL(join(here, '..', '..', '.build', 'core', 'dist', 'model-credentials', 'store.js')).href);

  const runResults = [];
  for (const [ordinal, spec] of runSpecs.entries()) {
    const directory = `${runRoot}-${spec.name}`;
    const specDriver = { ...driver, maxRequests: spec.maxRequests };
    const specSqlite = { ...sqlite, directory, runId: 'l1-' + spec.name };
    const specModel = spec.explicitJson
      ? { ...modelPi, transport: { [modelId]: 'json' } }
      : modelPi;
    const proposals = {
      proposals: [{
        id: `l1-${spec.name}`,
        planKind: 'staged/1',
        payload: {
          nextCursor: '1',
          phases: [{ id: 'turn', activations: [{
            id: `l1-${spec.name}/activation`,
            actorId: spec.actorId,
            momentId: spec.momentId,
            source: 'l1-probe',
            driver: 'llm/v1',
            input: { probe: spec.name, mode: spec.mode, instruction: instructionFor(spec.mode) },
          }] }],
        },
      }],
    };
    patchesPath = join(process.env.TMPDIR, `l1-patches-${process.pid}-${ordinal}.json`);
    await writeFile(patchesPath, JSON.stringify([
      { id: 'sqlite', config: specSqlite },
      { id: 'prompts', config: prompts },
      { id: 'driver-llm', config: specDriver },
      { id: 'model-pi', config: specModel },
      { id: 'round-robin', disabled: true },
      { id: 'generation', insert: [{ id: 'l1-proposals', name: '@society0/core/proposal-sequence', config: proposals }] },
    ], null, 2));
    const before = requests.length;
    let status = 'completed';
    try {
      await runProfile({
        profile: join(here, '..', '..', 'profiles', 'social-sqlite.json'),
        directory,
        lineageDirectory: directory + '-lineage',
        runId: 'l1-' + spec.name,
        patches: patchesPath,
        lock: lockPath,
        allowModelRequests: true,
        configureHost: async ctx => {
          await ctx.modelCredentials.register('authorized-local', new FileCredentialStore(credentialsPath));
        },
      });
    } catch (error) {
      status = String(error?.message ?? error);
      await log(`run ${spec.name} stopped: ${status.split('\n')[0]}`);
    }
    for (let index = before; index < requests.length; index++) requests[index].probe = { name: spec.name, actorId: spec.actorId, momentId: spec.momentId, mode: spec.mode };
    runResults.push({ ...spec, directory, status, requests: requests.length - before });
    await rm(patchesPath, { force: true }).catch(() => {});
  }

  const main = requests.filter((record, index) => index < mainCalls);
  const jsonRequest = requests.find(record => record.stream === false && record.index >= mainCalls);
  const toolCallCount = main.filter(record => (record.response.toolCalls ?? []).length > 0).length;
  const contextLimit = /INFERENCE_CONTEXT_LIMIT/.test(await readFile(liveLog, 'utf8'));
  const maxPrompt = Math.max(...main.map(record => record.response.usage.prompt_tokens ?? 0), 0);
  const toolDefinitionsValid = requests.every(record => record.response.toolCalls.every(call => call?.function?.name === 'act' || ['read', 'find', 'grep', 'bash'].includes(call?.function?.name)));

  assert.ok(requests.length <= maxCalls, `requests ${requests.length} exceed ${maxCalls}`);
  assert.equal(main.length, mainCalls, `expected ${mainCalls} main requests, got ${main.length}`);
  assert.ok(toolCallCount >= 9, `tool_calls ${toolCallCount}/10 below 9`);
  assert.equal(contextLimit, false, 'INFERENCE_CONTEXT_LIMIT observed');
  assert.ok(maxPrompt <= 8192, `prompt tokens ${maxPrompt} exceed 8k`);
  assert.ok(jsonRequest, 'explicit json transport request missing');
  assert.equal(jsonRequest.stream, false);

  const slotStats = name => {
    const values = main.map(record => record.slots[name]).filter(value => typeof value === 'number');
    const mean = values.reduce((sum, value) => sum + value, 0) / Math.max(values.length, 1);
    return { mean: Math.round(mean * 100) / 100, max: Math.max(...values, 0), budget: contextBudget().slot(name) };
  };
  const summary = {
    at: new Date().toISOString(),
    model: modelId,
    baseUrl: 'http://127.0.0.1:4000/v1',
    runRoot,
    runResults,
    requests: requests.length,
    mainRequests: main.length,
    toolCalls: toolCallCount,
    legalToolCalls: requests.filter(record => record.toolCallLegal.every(Boolean)).length,
    pseudoToolCalls: requests.filter(record => record.pseudoToolCall).length,
    maxPromptTokens: maxPrompt,
    explicitJson: jsonRequest ? { stream: jsonRequest.stream, toolCalls: jsonRequest.response.toolCalls } : null,
    slots: Object.fromEntries(['header', 'baseline', 'stateDelta', 'actions', 'percepts', 'history', 'output'].map(name => [name, slotStats(name)])),
    budget: contextBudget().tokens,
    modelToolsValid: toolDefinitionsValid,
    calls: requests.map((record, index) => ({
      index: index + 1,
      probe: record.probe,
      tools: record.tools,
      system: text(record.system?.[0]).slice(0, 160),
      toolCalls: record.response.toolCalls.map(call => ({ name: call.function?.name, arguments: call.function?.arguments })),
      legal: record.toolCallLegal,
      pseudo: record.pseudoToolCall,
      stopReason: record.response.finishReason,
      promptTokens: record.response.usage?.prompt_tokens ?? null,
      completionTokens: record.response.usage?.completion_tokens ?? null,
      slots: record.slots,
      toolSchemaBytes: record.toolSchemaBytes,
    })),
  };
  await writeEvidence('requests.json', requests.map(record => ({ ...record, headers: Object.fromEntries(Object.entries(record.headers).map(([key, value]) => [key, key.toLowerCase() === 'authorization' ? '***' : value])) })), secret);
  await writeFile(join(evidence, 'system-first.txt'), text(requests[0]?.system?.[0]) + '\n');
  await writeEvidence('summary.json', summary, secret);
  await log(`OK requests=${requests.length} toolCalls=${toolCallCount} maxPrompt=${maxPrompt} slots=${JSON.stringify(summary.slots)}`);
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
