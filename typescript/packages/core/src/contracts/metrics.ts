/** C0 指标合同：只有接口，没有任何实现依赖；宿主插件负责汇总与落盘。 */
export interface MetricsCounters {
    /** 单调递增的计数；同一 tick 内累加。 */
    count(name: string, value?: number): void;
    /** 观测值；汇总器按 tick 保留分位数所需的样本。 */
    observe(name: string, value: number): void;
    /** 最近值（例如 rss、lag），同一 tick 内后写覆盖先写。 */
    gauge(name: string, value: number): void;
}

/** 运行控制器使用的 tick 入口；sequence 是完整点的 completedCount。 */
export interface MetricsTick {
    readonly sequence: string;
    readonly logicalTime?: unknown;
    readonly sqlRowsRead?: number;
}

export interface MetricsRecorder extends MetricsCounters {
    /** tick 结束：写出一行 metrics.jsonl 并重置本 tick 计数。 */
    tick(input: MetricsTick): Promise<void>;
}

export type MetricsTickInput = MetricsTick;

/** 读取可选指标服务；未安装时返回 undefined，调用方不得因此改变业务结果。 */
export function metricsOf(context: { get(name: string): unknown }): MetricsRecorder | undefined {
    return context.get('metrics') as MetricsRecorder | undefined;
}

declare module '@society0/host' {
    interface Context {
        readonly metrics?: MetricsRecorder;
    }
}
