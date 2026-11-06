/**
 * B1 rule-1k（DESIGN §6.2）：1000 主体、规则驱动、50 tick。
 * 不调用 LLM；每 tick 墙钟与计数器写入 metrics.jsonl，供 perf-curve 与 baseline 使用。
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
    const ticks = options.ticks ?? 50;
    const f = await fixture({ actors, ticks, work: 'constant', checkpointEvery: options.checkpointEvery ?? 1, directory: options.directory });
    let result, rows;
    try {
        result = await f.run();
        rows = await f.metrics();
    }
    finally { await f.host.close(); }
    const wall = rows.map(row => row.wallMs);
    const curve = analyzeMetrics(rows, options.curve ?? {});
    const report = {
        schemaVersion: 1,
        mode: 'rule-1k',
        generatedAt: new Date().toISOString(),
        scale: { actors, ticks, checkpointEvery: options.checkpointEvery ?? 1 },
        operations: {
            run: { samples: rows.length, p50Ms: percentile(wall, 50), p95Ms: percentile(wall, 95), p99Ms: percentile(wall, 99), errors: result.status === 'completed' ? 0 : 1, status: result.status, reason: result.reason ?? null },
            total: { wallMs: performance.now() - started }
        },
        counters: {
            activations: rows.reduce((total, row) => total + row.activations.total, 0),
            sqlRowsRead: rows.reduce((total, row) => total + row.sqlRowsRead, 0),
            ticks: rows.length,
            curve: { status: curve.status, fastest: curve.fastest, violations: curve.violations.map(item => item.name) }
        },
        perTick: rows.map(row => ({ tick: row.tick, wallMs: row.wallMs, cpuMs: row.cpuMs, sqlRowsRead: row.sqlRowsRead, activations: row.activations.total })),
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
        checkpointEvery: value('checkpoint') ? Number(value('checkpoint')) : undefined,
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
