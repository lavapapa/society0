import type {Context} from '@society0/cordis';
import type {CompositionPlan} from '../profile-loader/index.js';
import type {BranchPlan,BranchSourcePort,BranchPlanReference} from './ports.js';
import '../run-directory/index.js';
/** Private files precede run services; the native session mounts the complete profile in phases. */
export async function createBranch(ctx:Context,plan:BranchPlan,generation:CompositionPlan,source:BranchSourcePort,planReference:BranchPlanReference){
 if(ctx.runDirectory.list().some(run=>run.runId===plan.target.targetRunId))throw Error('RUN_EXISTS');
 const request={plan,generation,planReference};const point=await source.prepare(request);
 const handle=await ctx.runDirectory.create({runId:plan.target.targetRunId,lineage:{kind:'existing'},generation,mount:plan.target.mount,prepare:async(_target,phase)=>{if(!phase)throw Error('BRANCH_PHASE_REQUIRED');return source.open(request,phase,point)}});
 return {handle,point};
}
