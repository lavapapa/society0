/**
 * B2 fake-llm-1k（DESIGN §6.2）：1000 主体、20 tick、fake provider。
 * fake provider 用确定性延迟模型（a + b×promptTok，带 KV 超限惩罚），
 * 不发起网络请求；延迟只记录不 sleep，避免基准本身被墙钟等待淹没。
 * usage、请求数与工具调用经 C0 指标与服务计数输出。
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
import { fixture, REQUIRED_REPORT_FIELDS } from './harness.mjs';
import { analyzeMetrics } from './perf-curve.mjs';
import { compareBaseline, loadBaseline } from './baseline-gate.mjs';

export { REQUIRED_REPORT_FIELDS };

function percentile(values, p) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p / 100 * sorted.length) - 1))];
}

export async function runBench(options = {}) {
    const started = performance.now();
    const actors = options.actors ?? 1000;
    const ticks = options.ticks ?? 20;
    const fakeLLM = {
        latencyBaseMs: options.latencyBaseMs ?? 120,
        latencyPerTokenMs: options.latencyPerTokenMs ?? 0.08,
        kvThreshold: options.kvThreshold ?? 8192,
        kvPenalty: options.kvPenalty ?? 2000,
        promptTokens: options.promptTokens ?? 1024
    };
    const f = await fixture({ actors, ticks, work: 'fake-llm', checkpointEvery: options.checkpointEvery ?? 1, fakeLLM, directory: options.directory });
    let result, rows;
    try {
        result = await f.run();
        rows = await f.metrics();
    }
    finally { await f.host.close(); }
    const curve = analyzeMetrics(rows, options.curve ?? {});
    const activations = f.counters.activations;
    const report = {
        schemaVersion: 1,
        mode: 'fake-llm-1k',
        generatedAt: new Date().toISOString(),
        scale: { actors, ticks, provider: 'fake', ...fakeLLM },
        operations: {
            run: { samples: rows.length, p50Ms: percentile(rows.map(row => row.wallMs), 50), p95Ms: percentile(rows.map(row => row.wallMs), 95), errors: result.status === 'completed' ? 0 : 1, status: result.status, reason: result.reason ?? null },
            modeledLatency: { p50Ms: f.model.modeledLatencyMs, p95Ms: f.model.modeledLatencyMs, source: 'deterministic model, not slept' },
            total: { wallMs: performance.now() - started }
        },
        counters: {
            activations,
            llmRequests: f.counters.llmRequests,
            promptTokens: f.counters.promptTokens,
            outputTokens: f.counters.outputTokens,
            cacheReadTokens: f.counters.cacheRead,
            toolCalls: f.counters.toolCalls,
            // 本基准的 fake driver 每个激活一个事务；提交数来自该事实，而不是 C1 计数器（尚未合并）。
            commitsPerActivation: activations ? 1 : 0,
            ticks: rows.length,
            curve: { status: curve.status, fastest: curve.fastest, violations: curve.violations.map(item => item.name) }
        },
        perTick: rows.map(row => ({
            tick: row.tick, wallMs: row.wallMs, cpuMs: row.cpuMs, sqlRowsRead: row.sqlRowsRead,
            activations: row.activations.total, llmRequests: row.llm.requests, promptTokens: row.llm.promptTokens,
            cacheReadTokens: row.llm.cacheReadTokens, tools: row.tools.calls
        })),
        durationMs: performance.now() - started
    };
    const baseline = await loadBaseline(options.baseline ?? new URL('./baseline.json', import.meta.url));
    report.gate = compareBaseline(report, baseline, options.threshold);
    return report;
}

async function main() {
    const argv = process.argv.slice(2);
    const value = name => { const index = argv.indexOf('--' + name); return index < 0 ? undefined : argv[index + 1]; };
    const report = await runBench({
        actors: value('actors') ? Number(value('actors')) : undefined,
        ticks: value('ticks') ? Number(value('ticks')) : undefined,
        promptTokens: value('prompt') ? Number(value('prompt')) : undefined,
        directory: value('directory')
    });
    if (value('out')) {
        await mkdir(dirname(resolve(value('out'))), { recursive: true });
        await writeFile(resolve(value('out')), JSON.stringify(report, null, 2) + '\n');
    }
    console.log(JSON.stringify(report));
    if (report.gate.status === 'fail') process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
