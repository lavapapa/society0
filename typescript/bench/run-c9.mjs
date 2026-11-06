/**
 * C9 基准套件入口：顺序跑 B1/B2/B3，合并成一份报告。
 * 用法：node bench/run-c9.mjs --out <report.json> [--update-baseline]
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { runBench as rule } from './rule-1k.mjs';
import { runBench as fake } from './fake-llm-1k.mjs';
import { runBench as history } from './history.mjs';
import { baselineEntry } from './baseline-gate.mjs';

export async function runAll(options = {}) {
    const started = performance.now();
    const report = { schemaVersion: 1, mode: 'c9-suite', generatedAt: new Date().toISOString(), benches: {} };
    for (const [name, bench] of [['B1-rule-1k', rule], ['B2-fake-llm-1k', fake], ['B3-history', history]]) {
        const benchStarted = performance.now();
        try {
            report.benches[name] = await bench({
                ...(options[name] ?? {}), baseline: options.baseline
            });
            report.benches[name].benchDurationMs = performance.now() - benchStarted;
        }
        catch (error) {
            report.benches[name] = { status: 'failed', error: String(error?.stack ?? error), benchDurationMs: performance.now() - benchStarted };
        }
    }
    report.durationMs = performance.now() - started;
    return report;
}

async function main() {
    const argv = process.argv.slice(2);
    const value = name => { const index = argv.indexOf('--' + name); return index < 0 ? undefined : argv[index + 1]; };
    const report = await runAll({ baseline: value('baseline') });
    if (value('out')) {
        const out = resolve(value('out'));
        await mkdir(dirname(out), { recursive: true });
        await writeFile(out, JSON.stringify(report, null, 2) + '\n');
    }
    if (argv.includes('--update-baseline')) {
        const path = value('baseline') ?? fileURLToPath(new URL('./baseline.json', import.meta.url));
        const baseline = JSON.parse(await readFile(path, 'utf8'));
        for (const [name, item] of Object.entries(report.benches)) {
            if (item?.status === 'failed' || !item?.mode) continue;
            baseline.modes[item.mode] = baselineEntry(item);
        }
        await writeFile(path, JSON.stringify(baseline, null, 2) + '\n');
    }
    console.log(JSON.stringify({ durationMs: report.durationMs, benches: Object.fromEntries(Object.entries(report.benches).map(([name, item]) => [name, { status: item.status ?? 'measured', durationMs: item.benchDurationMs, curve: item.counters?.curve, gate: item.gate }])) }));
    if (Object.values(report.benches).some(item => item.status === 'failed')) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
