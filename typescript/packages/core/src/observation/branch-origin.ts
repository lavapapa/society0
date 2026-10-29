import {realpath} from 'node:fs/promises';
import {resolve,sep} from 'node:path';
import type {BranchOrigin,BranchPreparation} from '@society0/host/run-branch';
export const ORIGIN_MAX_BYTES=1048576;
/** Resolve durable host storage before any private world materialization. */
export async function branchOrigin({plan,planReference}:BranchPreparation,sourceDirectory:string):Promise<BranchOrigin>{
 if(planReference.operationId!==plan.operationId)throw Error('BRANCH_PLAN_REFERENCE');
 const journalPath=await realpath(planReference.journalPath);
 requireIndependentJournal(journalPath,await realpath(sourceDirectory));
 requireIndependentJournal(journalPath,resolve(plan.target.targetDirectory));
 const origin:BranchOrigin={format:'society0/branch-origin/1',operationId:plan.operationId,source:{runId:plan.sourceRunId,pointId:plan.point.id},target:{runId:plan.target.targetRunId,generation:plan.target.generation,controlSequence:plan.target.controlSequence},plan:{journalPath,operationId:plan.operationId}};
 if(Buffer.byteLength(JSON.stringify(origin))>ORIGIN_MAX_BYTES)throw Error('BRANCH_ORIGIN_BUDGET');
 return origin;
}
function requireIndependentJournal(journalPath:string,worldDirectory:string){
 if(journalPath===worldDirectory||journalPath.startsWith(worldDirectory+sep))throw Error('BRANCH_JOURNAL_LIFETIME');
}
