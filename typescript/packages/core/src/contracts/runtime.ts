import type { Context } from '@society0/host';
import type { Json, Invocation, Disposer, Lease } from './common.js';
import type { WriteLease } from '../execution-fence/index.js';
import type { SqliteDatabase } from '../sqlite/index.js';
export type { Json, Invocation, Disposer, Lease };
export interface ActivationSignal {
    readonly id: string;
    readonly source: string;
    readonly input: Json;
}
export interface ActivationIntent {
    readonly signals?: readonly ActivationSignal[];
    readonly id: string;
    readonly actorId: string;
    readonly momentId: string;
    readonly source: string;
    readonly driver: string;
    readonly input: Json;
    /** 可选的准入 token 估算；KV 准入模式下由发起方填写。 */
    readonly estimatedTokens?: number;
    /** 旁路/后台调用标记；foreground 优先取用准入额度。 */
    readonly priority?: 'foreground' | 'background';
    /** 行动回执转交的唤醒意图；普通调度意图省略。 */
    readonly cause?: 'notify';
    /** notify 通知的原因；由 D8 在转交时填入，普通调度意图省略。 */
    readonly reason?: 'message' | 'request' | 'mention';
    /** 发起行动的主体；notify 通知用于每对主体的轮次上限。 */
    readonly from?: string;
}
export type ActivationAdmission = {
    readonly kind: 'accepted';
    readonly key: string;
} | {
    readonly kind: 'coalesced';
    readonly into: string;
    readonly mergedInput: Json;
} | {
    readonly kind: 'rejected';
    readonly reason: string;
};
export interface ActivationContext extends Invocation {
    readonly ctx: Context;
    readonly actorId: string;
    readonly momentId: string;
    readonly logicalTime: Json;
    readonly writeLease: WriteLease;
    readonly database: SqliteDatabase;
    /** 本线程已送达的 offer key；由绑定 fold 后传入，用于区分 ACTION_EXPIRED 与 ACTION_NOT_OFFERED。 */
    readonly deliveredOffers?: ReadonlySet<string>;
    /** 自动恢复重跑时标记这是第几次恢复，以及被重跑的原始激活命名空间。 */
    readonly recovery?: { readonly attempt: number; readonly baseOperationId: string };
}
export interface ActivationResult {
    readonly requiredOperations?: readonly string[];
    readonly status: 'completed' | 'waiting' | 'incomplete';
    readonly value: Json;
    readonly evidence: Json;
    /** 激活身份；由 ActivationService 补齐，用于批次证据和 RunResult 摘要。 */
    readonly activationId?: string;
    readonly actorId?: string;
    readonly momentId?: string;
}
export interface Driver {
    activate(intent: ActivationIntent, context: ActivationContext): Promise<ActivationResult>;
}
export interface DriverRegistry {
    register(name: string, driver: Driver): Disposer;
    acquire(name: string, context: Invocation): Promise<Lease<Driver>>;
}
export interface ExecutionContext extends Invocation {
    readonly ctx: Context;
    readonly database: SqliteDatabase;
    readonly lease: WriteLease;
    readonly logicalTime: Json;
    /** 自动恢复重跑序号；0 或省略表示首次执行。 */
    readonly recoveryAttempt?: number;
    readonly selections: readonly {
        release(): Promise<void>;
    }[];
}
export interface ActivationService {
    submit(intent: ActivationIntent, context: ExecutionContext): Promise<ActivationAdmission>;
    drain(context: ExecutionContext): Promise<readonly ActivationResult[]>;
}
export interface LogicalClock {
    readonly codec: string;
    initial(): Json;
    compare(a: Json, b: Json): -1 | 0 | 1;
    advance(previous: Json, trigger: Json): Json;
}
export interface Proposal {
    readonly id: string;
    readonly cursor: Json;
    readonly planKind: string;
    readonly payload: Json;
    readonly observedRevision?: string;
    readonly reservation?: {
        readonly source: string;
        readonly id: string;
        readonly operationId: string;
    };
}
export type ProposalResult = {
    readonly kind: 'proposal';
    readonly proposal: Proposal;
} | {
    readonly kind: 'idle';
    readonly resume: Json;
} | {
    readonly kind: 'end';
    readonly reason: string;
};
export interface AdvanceResult {
    readonly status: 'settled' | 'waiting' | 'incomplete';
    readonly frontier: {
        readonly codec: string;
        readonly value: Json;
    };
    readonly requiredOperations: readonly string[];
    readonly evidence: Json;
}
export interface RuntimeDriver {
    readonly planKinds: readonly string[];
    execute(proposal: Proposal, context: ExecutionContext): Promise<AdvanceResult>;
    drain(reason: string): Promise<void>;
}
import type { CompletePoint } from '../sqlite-bootstrap/index.js';
export type { CompletePoint };
export interface ProposalSource {
    waitProposal(cursor: Json, signal: AbortSignal): Promise<ProposalResult>;
    acknowledge(proposal: Proposal, cut: CompletePoint): Promise<void>;
    release(proposal: Proposal): Promise<void>;
}
export interface CompletionPolicy {
    evaluate(result: AdvanceResult): {
        readonly complete: boolean;
        readonly reason: string;
    };
}
export interface CheckpointPolicy {
    choose(input: {
        readonly result: AdvanceResult;
        readonly previous: CompletePoint;
        readonly frontier: Json;
        readonly advancesSinceCut: string;
        readonly completion: {
            readonly complete: boolean;
            readonly reason: string;
        };
    }): 'publish-cut' | 'continue';
}
export type TerminationInput = {
    readonly kind: 'advance';
    readonly result: AdvanceResult;
    readonly cut?: CompletePoint;
    readonly logicalTime: Json;
} | {
    readonly kind: 'source-end';
    readonly reason: string;
    readonly currentCut: CompletePoint;
    readonly liveRevision: string;
    readonly logicalTime: Json;
} | {
    readonly kind: 'source-idle';
    readonly resume: Json;
    readonly currentCut: CompletePoint;
    readonly liveRevision: string;
} | {
    readonly kind: 'stop-request';
    readonly mode: 'graceful' | 'abort';
    readonly reason: string;
};
export interface TerminationPolicy {
    evaluate(input: TerminationInput): {
        readonly action: 'continue' | 'pause' | 'stop' | 'fail';
        readonly reason: string;
    };
}
export interface GenerationView {
    readonly generation: string;
    readonly controlSequence: string;
    readonly clock: LogicalClock;
    readonly source: ProposalSource;
    readonly runtime: RuntimeDriver;
    readonly completion: CompletionPolicy;
    readonly checkpoint: CheckpointPolicy;
    readonly termination: TerminationPolicy;
}
export interface BoundaryLease {
    readonly generation: string;
    readonly cut: CompletePoint;
    finalize(outcome: {
        readonly kind: 'resume';
        readonly generation: string;
        readonly controlSequence: string;
    } | {
        readonly kind: 'halt';
        readonly reason: string;
    }): Promise<void>;
    dispose(): Promise<void>;
}
export interface ActivationPolicy {
    admit(intent: ActivationIntent, active: readonly ActivationIntent[]): ActivationAdmission;
}
export interface GenerationViews {
    register(view: GenerationView): Disposer;
    acquire(expected: string): Promise<Lease<GenerationView>>;
    current(): {
        readonly generation: string;
        readonly controlSequence: string;
    };
}
/** RunResult 的逐条激活摘要；只含计数与原因，不含提示词或工具正文。 */
export interface RunActivationSummary {
    readonly actorId: string;
    readonly tick: string;
    readonly reason: string;
    readonly requests: number;
    readonly toolCalls: number;
    readonly explorationErrors: number;
    readonly actRecoverable: number;
    /** 按领域拒绝结算的 act 数。 */
    readonly rejected: number;
    readonly actFailed: number;
    readonly requiredSatisfied: boolean;
    /** 预算耗尽时先用完的额度；未耗尽时省略。 */
    readonly limit?: 'requests' | 'toolCalls' | 'turns';
    /** 失败激活的事件时刻；成功摘要不写，避免扩大既有公开形状。 */
    readonly momentId?: string;
    /** 失败激活错误消息里的首个大写错误码，例如 SLOT_PENDING。 */
    readonly code?: string;
    /** 失败激活的触发阶段，例如 runtime.acquire。 */
    readonly stage?: string;
    /** SLOT_REQUIRED / SLOT_PENDING / SLOT_PROVIDER_MISSING 对应的槽位 key id。 */
    readonly slot?: string;
}
export interface RunActivationSummaries {
    readonly entries: readonly RunActivationSummary[];
    readonly truncated: boolean;
}
export interface RunResult {
    readonly status: 'completed' | 'paused' | 'failed';
    readonly cut?: CompletePoint;
    readonly reason?: string;
    readonly liveRevision?: string;
    readonly recoveryRequired?: boolean;
    /** 本次 run 内逐条激活摘要；有上限，超过时截断并置 truncated。 */
    readonly activations?: RunActivationSummaries;
}
export interface RunController {
    run(signal: AbortSignal): Promise<RunResult>;
    pauseAtBoundary(): Promise<BoundaryLease>;
    close(): Promise<void>;
}
