/**
 * perf-curve 检测器（DESIGN §4.2）。
 * 对 metrics.jsonl 中每个数值计数器做线性回归；斜率超过 maxSlopePerTick
 * （相对前 5 个 tick 的均值）即失败。再拟合二次项，二次项为正且 R² 改善
 * 超过 minR2Gain 时报告“疑似 O(T²)”。所有计数器分开回归，便于定位是
 * SQL、history 还是 thread。
 */
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

export const DEFAULT_OPTIONS = Object.freeze({ minTicks: 20, maxSlopePerTick: 0.02, minR2Gain: 0.2, baselineTicks: 5, minLinearR2: 0.5, maxTotalGrowth: 0.2, warmupTicks: 1, ignore: ['rssMb'] });

function flatten(value, prefix, out) {
    if (typeof value === 'number' && Number.isFinite(value)) { out[prefix] = value; return; }
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    for (const [key, item] of Object.entries(value)) flatten(item, prefix ? prefix + '.' + key : key, out);
}

/** 每行的数值计数器；bySource/byName 之类动态明细也保留，但零值序列会被跳过。 */
export function countersOf(row) {
    const out = {};
    flatten(row, '', out);
    delete out.tick;
    delete out.schemaVersion;
    // 顶层 sqlRowsRead 与 sqlite.rowsRead 是同一计数器；保留带 rowsRead 命名的一处。
    if (typeof out['sqlite.rowsRead'] === 'number') delete out.sqlRowsRead;
    return out;
}

function fit(values, degree) {
    const n = values.length;
    const xs = values.map((_, index) => index);
    const meanX = xs.reduce((a, b) => a + b, 0) / n;
    const meanY = values.reduce((a, b) => a + b, 0) / n;
    if (degree === 1) {
        let sxy = 0, sxx = 0;
        for (let index = 0; index < n; index++) { sxy += (xs[index] - meanX) * (values[index] - meanY); sxx += (xs[index] - meanX) ** 2; }
        const slope = sxx ? sxy / sxx : 0;
        const intercept = meanY - slope * meanX;
        let ssRes = 0, ssTot = 0;
        for (let index = 0; index < n; index++) { ssRes += (values[index] - (slope * xs[index] + intercept)) ** 2; ssTot += (values[index] - meanY) ** 2; }
        return { a: 0, b: slope, c: intercept, r2: ssTot ? 1 - ssRes / ssTot : 0 };
    }
    // 正规方程：y = a x² + b x + c
    const s = [0, 0, 0, 0, 0];
    const t = [0, 0, 0];
    for (let index = 0; index < n; index++) {
        const x = xs[index], y = values[index];
        s[0] += x ** 4; s[1] += x ** 3; s[2] += x ** 2; s[3] += x; s[4] += 1;
        t[0] += y * x ** 2; t[1] += y * x; t[2] += y;
    }
    const m = [[s[0], s[1], s[2], t[0]], [s[1], s[2], s[3], t[1]], [s[2], s[3], s[4], t[2]]];
    for (let col = 0; col < 3; col++) {
        let pivot = col;
        for (let row = col + 1; row < 3; row++) if (Math.abs(m[row][col]) > Math.abs(m[pivot][col])) pivot = row;
        if (Math.abs(m[pivot][col]) < 1e-12) return { a: 0, b: 0, c: meanY, r2: 0 };
        [m[col], m[pivot]] = [m[pivot], m[col]];
        for (let row = 0; row < 3; row++) {
            if (row === col) continue;
            const factor = m[row][col] / m[col][col];
            for (let k = col; k < 4; k++) m[row][k] -= factor * m[col][k];
        }
    }
    const a = m[0][3] / m[0][0], b = m[1][3] / m[1][1], c = m[2][3] / m[2][2];
    let ssRes = 0, ssTot = 0;
    for (let index = 0; index < n; index++) { ssRes += (values[index] - (a * xs[index] ** 2 + b * xs[index] + c)) ** 2; ssTot += (values[index] - meanY) ** 2; }
    return { a, b, c, r2: ssTot ? 1 - ssRes / ssTot : 0 };
}

function analyzeCounter(name, values, options) {
    if (values.length < options.minTicks) return undefined;
    if (values.every(value => value === 0)) return undefined;
    const baseCount = Math.min(options.baselineTicks, values.length);
    const base = values.slice(0, baseCount).reduce((a, b) => a + b, 0) / baseCount;
    const linear = fit(values, 1), quadratic = fit(values, 2);
    const reference = base || 0;
    const growth = reference > 0 ? linear.b / reference : (linear.b > 0 ? Number.POSITIVE_INFINITY : 0);
    const r2Gain = quadratic.r2 - linear.r2;
    const suspectQuadratic = quadratic.a > 0 && r2Gain > options.minR2Gain;
    // 线性拟合本身要有解释力；否则墙钟抖动会被当成增长（DESIGN 的回归显著性）。
    // 短窗口里固定开销会稀释“每 tick 2%”，因此再按窗口总增长判断：
    // 每 tick 线性增长意味着累计成本是二次的（O(T²)）。
    const significant = linear.r2 >= options.minLinearR2;
    const totalGrowth = reference > 0 ? linear.b * (values.length - 1) / reference : (linear.b > 0 ? Number.POSITIVE_INFINITY : 0);
    const flagged = significant && (
        (linear.b > 0 && growth > options.maxSlopePerTick) ||
        (linear.b > 0 && totalGrowth > options.maxTotalGrowth) ||
        suspectQuadratic
    );
    return {
        name, samples: values.length, base, slope: linear.b, growthPerTick: growth, totalGrowth,
        linearR2: linear.r2, quadraticR2: quadratic.r2, r2Gain, quadraticCoefficient: quadratic.a,
        // “quadratic” 在报告中表示“疑似超线性增长”：斜率超门槛或二次项显著都算。
        quadratic: flagged, suspectQuadratic, flagged
    };
}

/** 接受 metrics.jsonl 文本或行对象数组。 */
export function analyzeMetrics(input, overrides = {}) {
    const options = { ...DEFAULT_OPTIONS, ...overrides };
    const rows = (typeof input === 'string' ? input.trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : input)
        .slice().sort((a, b) => Number(a.tick ?? 0) - Number(b.tick ?? 0))
        .slice(options.warmupTicks);
    const names = new Set();
    const maps = new Map();
    for (const row of rows) {
        const counters = countersOf(row);
        for (const [name, value] of Object.entries(counters)) {
            names.add(name);
            if (!maps.has(name)) maps.set(name, []);
            maps.get(name).push(value);
        }
    }
    const counters = [...names].sort().map(name => analyzeCounter(name, maps.get(name), options)).filter(Boolean);
    const ignore = new Set(options.ignore ?? []);
    const violations = counters.filter(item => item.flagged && !ignore.has(item.name));
    const fastest = violations.slice().sort((a, b) => b.growthPerTick - a.growthPerTick)[0]?.name ?? null;
    return {
        schemaVersion: 1,
        ticks: rows.length,
        options,
        warmupTicks: options.warmupTicks,
        counters,
        violations,
        fastest,
        quadratic: violations.length > 0,
        status: violations.length ? 'fail' : 'pass'
    };
}

/** CLI：node bench/perf-curve.mjs <metrics.jsonl> [--out report.json] */
async function main() {
    const argv = process.argv.slice(2);
    const file = argv[0];
    if (!file) throw Error('用法：node bench/perf-curve.mjs <metrics.jsonl> [--out report.json]');
    const outIndex = argv.indexOf('--out');
    const report = analyzeMetrics(await readFile(file, 'utf8'));
    const text = JSON.stringify(report, null, 2) + '\n';
    if (outIndex >= 0 && argv[outIndex + 1]) {
        const { writeFile, mkdir } = await import('node:fs/promises');
        const { dirname } = await import('node:path');
        await mkdir(dirname(argv[outIndex + 1]), { recursive: true });
        await writeFile(argv[outIndex + 1], text);
    }
    console.log(JSON.stringify(report));
    if (report.status !== 'pass') process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
