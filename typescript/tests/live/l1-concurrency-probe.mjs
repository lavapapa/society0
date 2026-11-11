#!/usr/bin/env node
/** L1 并发探测：20 路并发 × 3 个短请求，只记录状态码/错误类型/关键响应头，不记录 key。 */
import {readFile, writeFile, mkdir, chmod, rm, appendFile} from 'node:fs/promises';
import {appendFileSync} from 'node:fs';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {performance} from 'node:perf_hooks';

const here = dirname(fileURLToPath(import.meta.url));
const W = join(here, '..', '..', '..', '..', '..');
const evidence = join(W, 'evidence', 'l1', 'concurrency');
const gatewayEnv = process.env.L1_GATEWAY_ENV;
if (!gatewayEnv) { console.error('L1_GATEWAY_ENV must point to the local LiteLLM gateway .env file'); process.exit(2); }
const modelId = 'Qwen3.6-35B-A3B-FP8';
const concurrency = 20;
const waves = 3;
const timeoutMs = 15 * 60 * 1000;

await mkdir(evidence, {recursive: true});
const logPath = join(evidence, 'probe.log');
await writeFile(logPath, '');
const log = async message => { await appendFile(logPath, message + '\n'); console.error(message); };
let exiting = false;
const finish = async code => { if (exiting) return; exiting = true; await appendFile(logPath, `EXIT=${code}\n`).catch(() => {}); };
process.on('exit', code => { if (!exiting) { try { appendFileSync(logPath, `EXIT=${code}\n`); } catch {} } });
process.on('uncaughtException', async error => { await log('FAIL ' + (error?.stack ?? error)); await finish(1); process.exit(1); });
process.on('unhandledRejection', async error => { await log('FAIL ' + (error?.stack ?? error)); await finish(1); process.exit(1); });
const watchdog = setTimeout(async () => { await log('FAIL watchdog'); await finish(124); process.exit(124); }, timeoutMs);

let credentialsPath;
const sanitizeError = value => {
  const item = value?.error ?? value;
  return {type: item?.type ?? null, code: item?.code ?? null, message: typeof item?.message === 'string' ? item.message.slice(0, 500) : null};
};
const interestingHeaders = headers => Object.fromEntries([...headers.entries()].filter(([name]) => /^(retry-after|x-litellm-|x-ratelimit-)/i.test(name)));
let secret;
try {
  const env = await readFile(gatewayEnv, 'utf8');
  const line = env.split(/\r?\n/).find(item => item.startsWith('LITELLM_MASTER_KEY='));
  if (!line) throw Error('LITELLM_MASTER_KEY missing');
  secret = line.slice('LITELLM_MASTER_KEY='.length).trim();
  if (!secret) throw Error('LITELLM_MASTER_KEY empty');
  if (!process.env.TMPDIR) throw Error('TMPDIR required');
  credentialsPath = join(process.env.TMPDIR, `l1-concurrency-key-${process.pid}`);
  await writeFile(credentialsPath, secret, {mode: 0o600});
  await chmod(credentialsPath, 0o600);

  const one = async index => {
    const started = performance.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(Error('probe timeout')), 120000);
    try {
      const response = await fetch('http://127.0.0.1:4000/v1/chat/completions', {
        method: 'POST',
        signal: controller.signal,
        headers: {'content-type': 'application/json', authorization: `Bearer ${secret}`},
        body: JSON.stringify({model: modelId, messages: [{role: 'user', content: 'Reply with exactly OK.'}], max_tokens: 64, stream: false, metadata: {session_id: `l1-concurrency-${index}`}}),
      });
      const text = await response.text();
      let body;
      try { body = JSON.parse(text); } catch { body = undefined; }
      return {
        index,
        status: response.status,
        durationMs: Math.round(performance.now() - started),
        headers: interestingHeaders(response.headers),
        ok: response.ok,
        error: response.ok ? null : sanitizeError(body ?? text),
        usage: body?.usage ?? null,
      };
    } catch (error) {
      return {index, status: null, durationMs: Math.round(performance.now() - started), headers: {}, ok: false, error: {type: 'probe_transport', code: String(error?.code ?? ''), message: String(error?.message ?? error).slice(0, 300)}, usage: null};
    } finally { clearTimeout(timer); }
  };

  const attempts = [];
  for (let wave = 0; wave < waves; wave++) {
    const batch = await Promise.all(Array.from({length: concurrency}, (_, lane) => one(wave * concurrency + lane)));
    attempts.push(...batch);
    const failed = batch.filter(item => !item.ok).length;
    await log(`wave ${wave + 1}: total=${batch.length} failed=${failed} statuses=${JSON.stringify(batch.reduce((acc, item) => { const key = String(item.status); acc[key] = (acc[key] ?? 0) + 1; return acc; }, {}))}`);
  }
  const byStatus = attempts.reduce((acc, item) => { const key = String(item.status); acc[key] = (acc[key] ?? 0) + 1; return acc; }, {});
  const failed = attempts.filter(item => !item.ok);
  const errors = failed.reduce((acc, item) => { const key = item.error?.type ?? String(item.status); acc[key] = (acc[key] ?? 0) + 1; return acc; }, {});
  const retryAfter = [...new Set(failed.flatMap(item => Object.entries(item.headers).filter(([name]) => /^retry-after$/i.test(name)).map(([, value]) => value)))];
  const durations = attempts.map(item => item.durationMs).sort((a, b) => a - b);
  const summary = {
    at: new Date().toISOString(),
    model: modelId,
    concurrency,
    waves,
    requests: attempts.length,
    ok: attempts.length - failed.length,
    failed: failed.length,
    errorRate: failed.length / attempts.length,
    byStatus,
    errors,
    retryAfter,
    latencyMs: {min: durations[0], p50: durations[Math.floor(durations.length * 0.5)], p95: durations[Math.floor(durations.length * 0.95)], max: durations.at(-1)},
    attempts,
  };
  await writeFile(join(evidence, 'summary.json'), JSON.stringify(summary, null, 2));
  await log(`OK requests=${summary.requests} failed=${summary.failed} errorRate=${summary.errorRate.toFixed(3)} statuses=${JSON.stringify(byStatus)} errors=${JSON.stringify(errors)}`);
  await finish(0);
} catch (error) {
  await log('FAIL ' + (error?.stack ?? error));
  await finish(1);
  process.exitCode = 1;
} finally {
  clearTimeout(watchdog);
  if (credentialsPath) await rm(credentialsPath, {force: true}).catch(() => {});
}
