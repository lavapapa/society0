#!/usr/bin/env node
/**
 * L2 运行目录检查：工具表、线程前缀、tool_failed、8k 预算、act 世界变化。
 * 只读；可在运行结束后对热目录/镜像目录/证据目录执行。
 *
 * node tests/live/l2-check.mjs --hot-root /var/tmp/society0-next-l2-... [--evidence <W>/evidence-dir/l2]
 */
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {readFile, writeFile} from 'node:fs/promises';
import {join, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {DatabaseSync} from 'node:sqlite';

const here = dirname(fileURLToPath(import.meta.url));
const W = join(here, '..', '..', '..', '..', '..');
const {values} = parseArgs({options: {
  'hot-root': {type: 'string'},
  evidence: {type: 'string'},
}});
const evidence = resolve(values.evidence ?? join(W, 'evidence', 'l2'));
const hotRoot = values['hot-root'] ? resolve(values['hot-root']) : null;
const failures = [];
const notes = [];
const check = (condition, message) => { if (!condition) failures.push(message); };

const requestsPath = join(evidence, 'requests.json');
const requestsJsonlPath = join(evidence, 'requests.jsonl');
const ticksPath = join(evidence, 'ticks.json');
let requests = existsSync(requestsPath) ? JSON.parse(await readFile(requestsPath, 'utf8').catch(() => '[]')) : [];
if (!requests.length && existsSync(requestsJsonlPath)) requests = (await readFile(requestsJsonlPath, 'utf8')).split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
const ticks = existsSync(ticksPath) ? JSON.parse(await readFile(ticksPath, 'utf8')) : [];
check(requests.length > 0, 'requests.json missing or empty');

// (a) 每次请求的工具表恰为 5 个工具。
const expected = ['bash', 'read', 'find', 'grep', 'act'];
for (const request of requests) {
  check(Array.isArray(request.tools) && request.tools.length === 5, `request ${request.seq}: tools ${JSON.stringify(request.tools)}`);
  for (const name of expected) check(request.tools?.includes(name), `request ${request.seq}: missing tool ${name}`);
}

// (b) 同一线程中后一请求以前一请求为前缀。
const byThread = new Map();
for (const request of requests) {
  if (!request.threadId) { notes.push(`request ${request.seq}: missing thread id, excluded from prefix check`); continue; }
  const list = byThread.get(request.threadId) ?? [];
  list.push(request);
  byThread.set(request.threadId, list);
}
const prefixExceptions = [];
for (const [threadId, list] of byThread) {
  list.sort((a, b) => a.seq - b.seq);
  for (let index = 1; index < list.length; index++) {
    const previous = list[index - 1];
    const current = list[index];
    const previousText = JSON.stringify(previous.messages ?? []);
    const currentMessages = current.messages ?? [];
    const prefix = currentMessages.length >= (previous.messages ?? []).length && JSON.stringify(currentMessages.slice(0, (previous.messages ?? []).length)) === previousText;
    if (!prefix) {
      // 允许同一线程因预算轮转而重新开始；必须记录原因。
      const reason = current.messages?.length === 0 ? 'empty_request' : 'thread_restarted_or_history_rewritten';
      prefixExceptions.push({threadId, previous: previous.seq, current: current.seq, reason});
      failures.push(`prefix broken thread=${threadId} ${previous.seq}->${current.seq} (${reason})`);
    }
  }
}

// (c) 不出现 tool_failed。RunResult 不暴露逐 activation 结果；tick completed 即没有
// incomplete/tool_failed 激活（termination 会把 incomplete 转成 pause），另扫描请求证据。
check(ticks.length === 5, `expected 5 ticks, got ${ticks.length}`);
for (const tick of ticks) check(tick.status === 'completed', `tick ${tick.tick}: status ${tick.status}`);
const toolFailedHits = requests.filter(request => /tool_failed/.test(JSON.stringify(request)));
check(toolFailedHits.length === 0, `tool_failed found in ${toolFailedHits.length} request(s)`);
const activations = [];

// (d) 每次请求估算 ≤8k，且没有 INFERENCE_CONTEXT_LIMIT。
for (const request of requests) {
  const estimate = request.slots?.total ?? 0;
  const prompt = Number(request.usage?.prompt_tokens ?? 0);
  check(estimate <= 8192, `request ${request.seq}: estimate ${estimate} > 8192`);
  check(prompt <= 8192, `request ${request.seq}: prompt_tokens ${prompt} > 8192`);
  check(!/INFERENCE_CONTEXT_LIMIT/.test(JSON.stringify(request)), `request ${request.seq}: INFERENCE_CONTEXT_LIMIT`);
}
for (const tick of ticks) {
  check(!/INFERENCE_CONTEXT_LIMIT/.test(JSON.stringify(tick)), `tick ${tick.tick}: INFERENCE_CONTEXT_LIMIT`);
}
for (const tick of ticks) check(tick.maxEstimate === undefined || tick.maxEstimate <= 8192, `tick ${tick.tick}: maxEstimate ${tick.maxEstimate}`);

// (e) social.publish 与 sendPartnerMessage 都至少经 act 成功一次，并有世界变化。
let world = {posts: [], messages: [], receipts: []};
if (hotRoot) {
  const worldPath = join(hotRoot, 'run', 'world.sqlite');
  check(existsSync(worldPath), `world.sqlite missing: ${worldPath}`);
  if (existsSync(worldPath)) {
    const db = new DatabaseSync(worldPath, {readOnly: true});
    try {
      world = {
        posts: db.prepare('SELECT id,author_id,content FROM social_posts ORDER BY ordinal').all(),
        messages: db.prepare('SELECT id,sender,receiver,content FROM social_messages ORDER BY ordinal').all(),
        receipts: db.prepare('SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id').all(),
      };
    } finally { db.close(); }
  }
} else {
  for (const tick of ticks) {
    world.posts.push(...Array.from({length: tick.postsDelta ?? 0}, (_, index) => ({id: `tick-${tick.tick}-post-${index}`})));
    world.messages.push(...Array.from({length: tick.messagesDelta ?? 0}, (_, index) => ({id: `tick-${tick.tick}-message-${index}`})));
  }
}
const receiptActions = world.receipts.map(row => String(row.action));
const publishReceipts = receiptActions.filter(action => action.includes('social.publish'));
const messageReceipts = receiptActions.filter(action => action.includes('social.sendPartnerMessage'));
check(publishReceipts.length >= 1, 'no successful social.publish act receipt');
check(messageReceipts.length >= 1, 'no successful social.sendPartnerMessage act receipt');
check(world.posts.length >= 1, 'no social_posts world change');
check(world.messages.length >= 1, 'no social_messages world change');

const result = {
  at: new Date().toISOString(),
  evidence,
  hotRoot,
  requests: requests.length,
  threads: byThread.size,
  activations: activations.length,
  prefixExceptions,
  world: {posts: world.posts.length, messages: world.messages.length, receipts: world.receipts.length},
  receiptActions: [...new Set(receiptActions)],
  failures,
  notes,
};
await writeFile(join(evidence, 'check.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
if (failures.length) {
  console.error('L2_CHECK_FAIL ' + failures.length + ' failure(s)');
  process.exitCode = 1;
} else {
  console.error('L2_CHECK_OK requests=' + requests.length + ' threads=' + byThread.size);
}
