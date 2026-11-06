/**
 * 每 tick 指标插件（DESIGN §6.1）。
 * 汇总器由 host diagnostics 提供；本插件负责运行目录、native 行读取差分，
 * 以及在不改 driver/model-pi 的前提下接入 LLM 请求、usage 与工具调用。
 */
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import { MetricsAggregator } from '@society0/host/diagnostics';
import type { MetricsRecorder, MetricsTick } from '../contracts/metrics.js';
import { INFERENCE_OBSERVER } from '../contracts/inference.js';
import type { AgentEvent, AgentMessage } from '@earendil-works/pi-agent-core';
import type { Invocation } from '../contracts/common.js';
import '../sqlite/index.js';

export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    title: 'metrics', summary: '每 tick 汇总 metrics.jsonl，接入 LLM usage 与工具调用', configVersion: 1,
    configPolicy: [], scope: 'run', allowedScopes: ['run'], hostAbi: 'society0/host/1'
};
export const api: ApiEntry = {
    exportPath: './metrics', provides: [{ key: 'metrics', protocol: 'metrics', major: 1, capabilities: [] }],
    requires: [
        { service: { key: 'database', protocol: 'database', major: 1, capabilities: [] }, optional: false, phase: 'module' },
        { service: { key: 'slots', protocol: 'slots', major: 1, capabilities: [] }, optional: true, phase: 'module' }
    ]
};

function bytes(value: unknown): number {
    try { return Buffer.byteLength(typeof value === 'string' ? value : JSON.stringify(value) ?? ''); } catch { return 0; }
}

function roleOf(message: AgentMessage): string {
    return (message as { role?: string }).role ?? '';
}

function usageOf(message: AgentMessage): { input?: number; output?: number; cacheRead?: number; cacheWrite?: number } | undefined {
    return (message as { usage?: { input?: number; output?: number; cacheRead?: number; cacheWrite?: number } }).usage;
}

export class Metrics extends Service implements MetricsRecorder {
    static Config = Config;
    static inject = ['database'];
    private aggregator: MetricsAggregator;
    private lastRows = 0;

    constructor(ctx: Context) {
        super(ctx, 'metrics');
        this.aggregator = new MetricsAggregator(ctx.database.directory);
        const slots = ctx.get('slots') as Context['slots'] | undefined;
        if (slots) {
            ctx.effect(() => slots.contribute(INFERENCE_OBSERVER, {
                id: 'metrics', value: {
                    observe: (event: AgentEvent, _context: Invocation) => {
                        if (event.type === 'message_end' && roleOf(event.message) === 'assistant') {
                            this.count('llm.requests');
                            const usage = usageOf(event.message);
                            if (usage) {
                                this.observe('llm.promptTokens', Number(usage.input ?? 0));
                                this.count('llm.outputTokens', Number(usage.output ?? 0));
                                this.count('llm.cacheReadTokens', Number(usage.cacheRead ?? 0));
                                this.count('llm.cacheWriteTokens', Number(usage.cacheWrite ?? 0));
                            }
                        }
                        else if (event.type === 'tool_execution_start') {
                            this.count('tools.calls');
                            if (event.toolName) this.count('tools.byName.' + event.toolName);
                        }
                        else if (event.type === 'tool_execution_end') this.observe('tools.resultBytes', bytes(event.result));
                    }
                }
            }));
        }
    }

    count(name: string, value = 1): void { this.aggregator.count(name, value); }
    observe(name: string, value: number): void { this.aggregator.observe(name, value); }
    gauge(name: string, value: number): void { this.aggregator.gauge(name, value); }

    async tick(input: MetricsTick): Promise<void> {
        let rowsRead = 0;
        try {
            const state = await this.ctx.database.nativeDiagnostics() as { sqlRowsRead?: number; rowsRead?: number };
            const total = state.sqlRowsRead ?? state.rowsRead;
            if (typeof total === 'number') {
                rowsRead = total - this.lastRows;
                this.lastRows = total;
            }
        }
        catch { /* native 统计不可用时只少一个计数器 */ }
        await this.aggregator.tick({ tick: input.sequence, logicalTime: input.logicalTime ?? null, sqlRowsRead: Math.max(0, rowsRead) });
    }

    snapshot() { return this.aggregator.snapshot(); }
}

export default Metrics;
