import type { Json, Disposer } from './common.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import type { TargetRef } from './affordance.js';
export type SqliteReader = Pick<SqliteReadView, 'query'>;
export type EligibilityRequest = {
    readonly actorId: string;
    readonly momentId: string;
    readonly logicalTime: Json;
} & ({
    readonly kind: 'activation';
    readonly intentId: string;
    readonly driver: string;
} | {
    readonly kind: 'action-discovery';
    readonly action: string;
    readonly target?: TargetRef;
} | {
    readonly kind: 'action';
    readonly action: string;
    readonly input: Json;
    readonly target?: TargetRef;
});
export interface EligibilityFinding {
    readonly allowed: boolean;
    readonly reason: string;
    readonly facts: Json;
    readonly versions: readonly {
        readonly owner: string;
        readonly version: string;
    }[];
}
export interface EligibilityRule {
    readonly id: string;
    /** 为 true 时按目标逐条评估；否则同一次 offers() 内沿用按行动名的结果。 */
    readonly targetAware?: boolean;
    evaluate(request: EligibilityRequest, reader: SqliteReader): Promise<EligibilityFinding>;
}
export interface EligibilityDecision {
    readonly allowed: boolean;
    readonly findings: readonly {
        readonly ruleId: string;
        readonly finding: EligibilityFinding;
    }[];
}
export interface ActorEligibility {
    register(rule: EligibilityRule): Disposer;
    evaluate(request: EligibilityRequest, reader: SqliteReader): Promise<EligibilityDecision>;
}
