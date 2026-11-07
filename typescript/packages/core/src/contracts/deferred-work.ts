/** C8 延后任务通道（DESIGN §5.2）的公共合同。 */
import type { Disposer, Json } from './common.js';
import type { ActivationAdmission, ActivationIntent, ExecutionContext } from './runtime.js';
import type { CommitReceipt, SqliteDatabase, SqliteMigration, SqliteTransaction, TransactionRequest } from '../sqlite/index.js';

export type DeferredJobState = 'queued' | 'running' | 'done' | 'failed';
export type DeferredPriority = 'background';

/** enqueue 时提交的意图；id 可省略，由 content hash 生成。 */
export interface DeferredJob {
    readonly id?: string;
    readonly kind: string;
    /** 输入引用；worker 只读地按此取快照。 */
    readonly inputs: Json;
    /** 产出的命名空间；await 按它等待。 */
    readonly produces: string;
    /** 消费方最多容忍的 tick 延迟。 */
    readonly maxLagTicks: number;
    /** 旁路 LLM 调用占 KV 准入额度时使用。 */
    readonly priority?: DeferredPriority;
}

export interface DeferredJobRecord extends DeferredJob {
    readonly id: string;
    readonly enqueuedTick: string;
    readonly state: DeferredJobState;
    readonly producedAt?: string;
    readonly output?: Json;
    readonly error?: string;
    readonly attempts: number;
}

export interface DeferredContext {
    readonly operationId: string;
    /** 产出所在的逻辑位置：登记该 job 的完整点 tick；不是墙钟。 */
    readonly producedAt: string;
    /** 同一逻辑位置，便于 handler 构造真实执行上下文。 */
    readonly logicalTime: Json;
    readonly runId: string;
    readonly signal: AbortSignal;
    readonly database: SqliteDatabase;
    transaction<T>(operationId: string, body: (tx: SqliteTransaction) => Promise<T>, request?: Partial<TransactionRequest>): Promise<{ value: T; receipt: CommitReceipt }>;
}

/** handler 的产出写入与 job 完成标记由 DeferredWork 放在同一个事务里执行。 */
export interface DeferredProduce {
    readonly output?: Json;
    write(tx: SqliteTransaction, job: DeferredJobRecord, context: DeferredContext): Promise<void>;
}

export type DeferredRunResult = Json | undefined | DeferredProduce;

export interface DeferredHandler {
    run(job: DeferredJobRecord, context: DeferredContext): Promise<DeferredRunResult>;
}

export interface DeferredWork {
    /** 在 complete 事务中登记；返回内容哈希得到的 job id。 */
    enqueue(job: DeferredJob): string;
    /** 等到 produces 命名空间内 enqueuedTick 小于 uptoTick 的 job 全部完成。 */
    await(ns: string, uptoTick: string | number): Promise<void>;
    register(kind: string, handler: DeferredHandler): Disposer;
    /** 旁路 LLM 调用：强制 priority=background，占用同一 KV 准入额度，driver 复用当前线程 Header/session。 */
    background(intent: ActivationIntent, context: ExecutionContext): Promise<ActivationAdmission>;
    /** 供 run-controller 在 complete 事务里调用。 */
    commit(tx: SqliteTransaction, tick: string): Promise<void>;
    /** 新 proposal 开始，丢弃上一个未提交的意图。 */
    begin(): void;
    /** proposal 未完成，丢弃未提交的意图。 */
    discard(): void;
    /** 启动已入队的 job；不阻塞调用方。 */
    wake(): void;
    pending(): readonly DeferredJob[];
    read(id: string): DeferredJobRecord | undefined;
    failures(): readonly DeferredJobRecord[];
    describe(): { readonly queued: number; readonly running: number; readonly completed: number; readonly failed: number };
}

export function deferredJobId(job: DeferredJob): string {
    const stable = (value: Json): string => {
        if (value === null || typeof value !== 'object') return JSON.stringify(value);
        if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
        const object = value as { readonly [key: string]: Json };
        return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + stable(object[key]!)).join(',') + '}';
    };
    const body = stable({ kind: job.kind, inputs: job.inputs, produces: job.produces, maxLagTicks: job.maxLagTicks });
    // 稳定内容哈希；不引入额外运行时依赖。
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let index = 0; index < body.length; index++) {
        const code = body.charCodeAt(index);
        h1 = Math.imul(h1 ^ code, 2654435761);
        h2 = Math.imul(h2 ^ code, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return 'dw-' + (h2 >>> 0).toString(16).padStart(8, '0') + (h1 >>> 0).toString(16).padStart(8, '0');
}

export function deferredWorkMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return {
        owner: 'deferred-work', from: 0, to: 1, after: [], source,
        statements: [{
            sql: `CREATE TABLE deferred_jobs(
id TEXT PRIMARY KEY NOT NULL,
kind TEXT NOT NULL,
inputs TEXT NOT NULL,
produces TEXT NOT NULL,
max_lag_ticks INTEGER NOT NULL,
enqueued_tick TEXT NOT NULL,
state TEXT NOT NULL,
produced_at TEXT,
output TEXT,
error TEXT,
attempts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX deferred_jobs_pending ON deferred_jobs(state,produces,enqueued_tick);
CREATE INDEX deferred_jobs_tick ON deferred_jobs(enqueued_tick,id);`
        }]
    };
}
