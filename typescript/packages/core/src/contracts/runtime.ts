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
}
export interface ActivationResult {
    readonly requiredOperations?: readonly string[];
    readonly status: 'completed' | 'waiting' | 'incomplete';
    readonly value: Json;
    readonly evidence: Json;
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
export interface RunResult {
    readonly status: 'completed' | 'paused' | 'failed';
    readonly cut?: CompletePoint;
    readonly reason?: string;
    readonly liveRevision?: string;
    readonly recoveryRequired?: boolean;
}
export interface RunController {
    run(signal: AbortSignal): Promise<RunResult>;
    pauseAtBoundary(): Promise<BoundaryLease>;
    close(): Promise<void>;
}
