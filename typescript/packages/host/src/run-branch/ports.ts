import type {Profile,CompositionPlan} from '../profile-loader/index.js';
import type {PreparedRun,RunRoot,RunSummary,RunPreparationPhase} from '../run-directory/index.js';
import type {Json,ProgramSource} from '../config-resolvers/types.js';
export interface BranchPoint extends RunRoot {readonly id:string;readonly runId:string;readonly revision:string;readonly generation:string;readonly schema:unknown;readonly frontier:unknown;readonly lineage?:unknown;readonly evidence?:unknown;readonly controlSequence:string}
export interface BranchProfile {
 readonly targetRunId:string;readonly targetDirectory:string;
 readonly generation:string;readonly controlSequence:string;
 readonly mount:{readonly runEntryId:string;readonly bootstrapEntryId:string};
 readonly profile:Profile;readonly baseURL:string;
 readonly migrations:Json;
 readonly seedPolicy:'preserve-seed-new-run-address'|'explicit-seed-new-run-address';
}
export interface BranchPlan {
 readonly format:'society0/branch-plan/1';readonly operationId:string;
 readonly sourceRunId:string;readonly point:BranchPoint;readonly target:BranchProfile;
 readonly sources:readonly ProgramSource[];readonly generationEvidence:Json;
 readonly sourceRandom?:RandomSelection;readonly targetRandom?:RandomSelection;
 readonly random:{readonly algorithm:'philox4x32-10/v1';readonly mapping:'length-prefixed-utf8-seed-seq/v1';readonly seedPolicy:BranchProfile['seedPolicy']};
}
/** The host-owned journal retains its immutable plan member for every dependent branch. */
export interface BranchPlanReference {readonly journalPath:string;readonly operationId:string}
export interface BranchOrigin {
 readonly format:'society0/branch-origin/1';readonly operationId:string;
 readonly source:{readonly runId:string;readonly pointId:string};
 readonly target:{readonly runId:string;readonly generation:string;readonly controlSequence:string};
 readonly plan:BranchPlanReference;
}
export interface BranchPreparation {readonly plan:BranchPlan;readonly generation:CompositionPlan;readonly planReference:BranchPlanReference}
/** Implemented inside the source run by Core, borrowed through a native consumer fiber. */
export interface BranchSourcePort {
 describe(pointId:string):Promise<BranchPoint>;
 prepare(request:BranchPreparation):Promise<BranchPoint>;
 open(request:BranchPreparation,phase:RunPreparationPhase,root:BranchPoint):Promise<PreparedRun & {root:BranchPoint}>;
 probe(plan:BranchPlan):Promise<{status:'prepared';root:BranchPoint}|{status:'not-prepared'}|{status:'unknown';reason:string}>;
}

export interface BranchReceipt extends RunSummary {readonly point:BranchPoint;readonly generation:string}

export interface RandomSelection {readonly algorithm:'philox4x32-10/v1';readonly mapping:'length-prefixed-utf8-seed-seq/v1';readonly seed:string}
