/**
 * bench/baseline.json 回归门槛（DESIGN §6.2）：同一模式同一指标退化超过
 * threshold（默认 10%）即失败。只比较明确的成本型指标，计数型指标不参与。
 */
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

export const DEFAULT_THRESHOLD = 0.1;

const COST = /(Ms|Read|durationMs|bytes|Bytes)/;

function flatten(value, prefix, out) {
    if (typeof value === 'number' && Number.isFinite(value)) { out[prefix] = value; return; }
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    for (const [key, item] of Object.entries(value)) flatten(item, prefix ? prefix + '.' + key : key, out);
}

export async function loadBaseline(source) {
    const path = source instanceof URL ? fileURLToPath(source) : source;
    try { return JSON.parse(await readFile(path, 'utf8')); }
    catch { return { schemaVersion: 1, modes: {} }; }
}

export function compareBaseline(report, baseline, threshold = DEFAULT_THRESHOLD) {
    const mode = report.mode;
    const previous = baseline?.modes?.[mode];
    if (!previous) return { status: 'baseline-missing', mode, threshold, regressions: [] };
    const current = flatten(report, '', {});
    const expected = flatten(previous, '', {});
    const regressions = [];
    for (const [name, value] of Object.entries(expected)) {
        if (!COST.test(name)) continue;
        const now = current[name];
        if (typeof now !== 'number' || !(value > 0)) continue;
        if (now > value * (1 + threshold)) regressions.push({ name, baseline: value, current: now, ratio: now / value });
    }
    return { status: regressions.length ? 'fail' : 'pass', mode, threshold, regressions };
}

/** 从一次完整报告生成可提交的基线片段。 */
export function baselineEntry(report) {
    const operations = {};
    if (report.operations?.run) operations.run = { p50Ms: report.operations.run.p50Ms, p95Ms: report.operations.run.p95Ms };
    if (report.operations?.total) operations.total = { wallMs: report.operations.total.wallMs };
    const counters = {};
    if (report.counters) {
        for (const key of ['sqlRowsRead', 'durationMs']) if (typeof report.counters[key] === 'number') counters[key] = report.counters[key];
    }
    if (typeof report.durationMs === 'number') counters.durationMs = report.durationMs;
    return { generatedAt: report.generatedAt, scale: report.scale, operations, counters };
}
