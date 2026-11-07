/**
 * host diagnostics 的每 tick 指标汇总器（DESIGN §6.1）。
 * 各插件通过 count/observe/gauge 上报；tick 结束时汇总成一行写入运行目录的
 * metrics.jsonl。单行硬上限 4KiB，超限时先丢弃明细对象再报错。
 * 这里只做观测，不参与领域事务，也不新增资源上限。
 */
import { appendFile } from 'node:fs/promises';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';

export interface MetricsCounters {
    count(name: string, value?: number): void;
    observe(name: string, value: number): void;
    gauge(name: string, value: number): void;
}

export interface MetricsTickInput {
    readonly tick: string;
    readonly logicalTime: unknown;
    /** native 层 sqlite3_stmt_status 自上一 tick 起的增量。 */
    readonly sqlRowsRead?: number;
}

export interface MetricsSnapshot {
    readonly counters: Readonly<Record<string, number>>;
    readonly observations: Readonly<Record<string, { count: number; sum: number; p50: number; p95: number }>>;
    readonly gauges: Readonly<Record<string, number>>;
}

export const MAX_METRICS_LINE_BYTES = 4096;
const MAX_OBSERVATIONS = 4096;

function percentile(sorted: readonly number[], fraction: number): number {
    if (!sorted.length) return 0;
    const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(fraction * sorted.length) - 1));
    return sorted[index]!;
}

function round(value: number, digits = 3): number {
    const scale = 10 ** digits;
    return Math.round(value * scale) / scale;
}

export class MetricsAggregator implements MetricsCounters {
    private counters = new Map<string, number>();
    private observations = new Map<string, number[]>();
    private gauges = new Map<string, number>();
    private lastWall = performance.now();
    private lastCpu = process.cpuUsage();
    private sequence = 0;

    constructor(private readonly directory?: string) {}

    count(name: string, value = 1): void {
        if (!name || !Number.isFinite(value) || value < 0) return;
        this.counters.set(name, (this.counters.get(name) ?? 0) + value);
    }

    observe(name: string, value: number): void {
        if (!name || !Number.isFinite(value)) return;
        const values = this.observations.get(name) ?? [];
        if (values.length < MAX_OBSERVATIONS) values.push(value);
        this.observations.set(name, values);
    }

    gauge(name: string, value: number): void {
        if (!name || !Number.isFinite(value)) return;
        this.gauges.set(name, value);
    }

    snapshot(): MetricsSnapshot {
        return {
            counters: Object.fromEntries(this.counters),
            observations: Object.fromEntries([...this.observations].map(([name, values]) => {
                const sorted = [...values].sort((a, b) => a - b);
                return [name, { count: values.length, sum: values.reduce((total, item) => total + item, 0), p50: percentile(sorted, 0.5), p95: percentile(sorted, 0.95) }];
            })),
            gauges: Object.fromEntries(this.gauges)
        };
    }

    /** 汇总并写一行；调用后本 tick 的计数清零。 */
    async tick(input: MetricsTickInput): Promise<void> {
        const now = performance.now();
        const cpu = process.cpuUsage();
        const wallMs = round(Math.max(0, now - this.lastWall));
        const cpuMs = round(Math.max(0, (cpu.user - this.lastCpu.user) + (cpu.system - this.lastCpu.system)) / 1000);
        this.lastWall = now;
        this.lastCpu = cpu;
        const row = this.row(input, wallMs, cpuMs);
        this.counters.clear();
        this.observations.clear();
        this.gauges.clear();
        if (!this.directory) return;
        const line = this.encode(row);
        await appendFile(join(this.directory, 'metrics.jsonl'), line + '\n');
    }

    private counter(name: string): number {
        return this.counters.get(name) ?? 0;
    }

    private grouped(prefix: string): Record<string, number> {
        const group: Record<string, number> = {};
        for (const [name, value] of this.counters) {
            if (!name.startsWith(prefix)) continue;
            const key = name.slice(prefix.length);
            if (key) group[key] = (group[key] ?? 0) + value;
        }
        return group;
    }

    private observed(name: string): { count: number; sum: number; p50: number; p95: number } {
        const values = this.observations.get(name) ?? [];
        const sorted = [...values].sort((a, b) => a - b);
        return { count: values.length, sum: values.reduce((total, item) => total + item, 0), p50: percentile(sorted, 0.5), p95: percentile(sorted, 0.95) };
    }

    private row(input: MetricsTickInput, wallMs: number, cpuMs: number): Record<string, unknown> {
        const prompt = this.observed('llm.promptTokens');
        const inputTokens = prompt.sum;
        const cacheRead = this.counter('llm.cacheReadTokens');
        const resultBytes = this.observed('tools.resultBytes');
        const rowsRead = Math.max(0, Math.trunc(input.sqlRowsRead ?? this.gauges.get('sqlRowsRead') ?? 0));
        return {
            schemaVersion: 1,
            tick: Number(input.tick),
            sequence: String(input.tick),
            logicalTime: input.logicalTime ?? null,
            wallMs,
            cpuMs,
            rssMb: round(process.memoryUsage().rss / 1048576, 3),
            activations: {
                total: this.counter('activations.total'),
                bySource: this.grouped('activations.bySource.'),
                notified: this.counter('activations.notified'),
                wakeDropped: this.counter('activations.wakeDropped')
            },
            llm: {
                requests: this.counter('llm.requests'),
                failedRequests: this.counter('llm.failedRequests'),
                promptTokens: inputTokens,
                promptTokP50: round(prompt.p50),
                promptTokP95: round(prompt.p95),
                outTok: this.counter('llm.outputTokens'),
                cacheReadTokens: cacheRead,
                cacheWriteTokens: this.counter('llm.cacheWriteTokens'),
                cacheReadRatio: inputTokens ? round(cacheRead / inputTokens, 4) : 0,
                latencyP50Ms: round(this.observed('llm.requestLatencyMs').p50),
                latencyP95Ms: round(this.observed('llm.requestLatencyMs').p95),
                queueWaitP95Ms: round(this.observed('llm.queueWaitMs').p95)
            },
            tools: {
                calls: this.counter('tools.calls'),
                byName: this.grouped('tools.byName.'),
                resultBytesP95: round(resultBytes.p95)
            },
            sqlite: {
                commits: this.counter('sqlite.commits'),
                fsyncs: this.counter('sqlite.fsyncs'),
                rowsRead
            },
            sqlRowsRead: rowsRead,
            history: {
                publishMs: round(this.counter('history.publishMs')),
                segments: this.counter('history.segments'),
                depsVisited: this.counter('history.depsVisited')
            },
            deferred: {
                queued: Math.trunc(this.gauges.get('deferred.queued') ?? this.counter('deferred.queued')),
                failed: Math.trunc(this.gauges.get('deferred.failed') ?? this.counter('deferred.failed')),
                lagTicks: Math.trunc(this.gauges.get('deferred.lagTicks') ?? this.counter('deferred.lagTicks'))
            }
        };
    }

    /** 固定字段优先；超 4KiB 时先丢明细，再失败。 */
    private encode(row: Record<string, unknown>): string {
        let line = JSON.stringify(row);
        if (Buffer.byteLength(line) <= MAX_METRICS_LINE_BYTES) return line;
        const activations = row.activations as { bySource: Record<string, number> };
        const tools = row.tools as { byName: Record<string, number> };
        const history = row.history as { publishMs: number };
        const trimmed: Record<string, unknown> = { ...row, truncated: true, activations: { ...activations, bySource: {} }, tools: { ...tools, byName: {} } };
        line = JSON.stringify(trimmed);
        if (Buffer.byteLength(line) <= MAX_METRICS_LINE_BYTES) return line;
        const minimal: Record<string, unknown> = { ...trimmed, history: { publishMs: history.publishMs }, deferred: { queued: 0, failed: 0, lagTicks: 0 } };
        line = JSON.stringify(minimal);
        if (Buffer.byteLength(line) > MAX_METRICS_LINE_BYTES) throw Error('METRICS_LINE_BUDGET');
        return line;
    }
}
