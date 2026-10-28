#!/usr/bin/env node
import {readFile,writeFile} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {applyEntryPatches} from '@society0/include';
import {runProfile} from './run-profile.mjs';

export async function forkProfile(options){
 const targetDirectory=resolve(options.targetDirectory),targetRunId=options.targetRunId;
 const targetURL=pathToFileURL(resolve(options.targetProfile));
 const profile=JSON.parse(await readFile(targetURL,'utf8'));
 if(options.lock)profile.lockfile=pathToFileURL(resolve(options.lock)).href;
 const leaves=entries=>entries.flatMap(entry=>entry.group?leaves(entry.config??[]):[entry]);
 const entries=leaves(applyEntryPatches([],profile.patches,message=>{throw Error('PROFILE_PATCH: '+message)}));
 const config=id=>entries.find(entry=>entry.id===id)?.config??{};
 return runProfile({...options,reopen:true,observe:true,branchDirectory:options.branchDirectory??join(dirname(targetDirectory),'branch-operations'),afterRun:async({host,ctx,cut})=>{
  const generation=options.generation??'g2',controlSequence=String(BigInt(cut.controlSequence)+1n),authority=ctx.attempts.authority();
  profile.patches.push(
   {id:'sqlite',config:{...config('sqlite'),directory:targetDirectory,runId:targetRunId,generation}},
   {id:'artifacts-local',config:{...config('artifacts-local'),directory:join(targetDirectory,'artifacts')}},
   {id:'sqlite-history',config:{...config('sqlite-history'),directory:targetDirectory,runId:targetRunId,generation,controlSequence}},
   {id:'attempts',config:{...config('attempts'),directory:authority.directory,lineage:authority.lineage}},
   {id:'generation-binding',config:{...config('generation-binding'),generation,controlSequence}}
  );
  const started=performance.now();
  const plan=await host.ctx.runBranch.plan(options.runId,cut.id,{targetRunId,targetDirectory,generation,controlSequence,mount:{runEntryId:'run',bootstrapEntryId:'bootstrap'},profile,baseURL:targetURL.href,migrations:[],seedPolicy:'preserve-seed-new-run-address'});
  const walk=entries=>entries.flatMap(entry=>[entry,...walk(entry.children??[])]);
  const active=walk(plan.generationEvidence.plan.entries).filter(entry=>!entry.disabled);
  const uses=name=>active.some(entry=>entry.name===name),model=uses('@society0/core/driver-llm');
  if(options.advanceTarget&&!options.offline&&uses('@society0/core/model-codex-subscription'))throw Error('CODEX_NOT_AUTHORIZED: target remains planned; subscription execution requires separate authorization');
  if(options.advanceTarget&&!options.offline&&model&&!options.allowModelRequests)throw Error('MODEL_NOT_AUTHORIZED: target remains planned; obtain explicit model authorization before execution');
  const planMs=performance.now()-started,creating=performance.now();
  const receipt=await host.ctx.runBranch.create(plan);const createMs=performance.now()-creating;
  const lease=host.ctx.runDirectory.acquire(targetRunId,options.advanceTarget&&!options.offline?'write':'read'),handle=lease.value;
  let report,consumer;
  try{
   let target;consumer=await lease.ctx.plugin({name:'public.branch.consume',inject:['runs','observation','history','attempts'],apply(ctx){target=ctx}});await consumer.await();
   const result=options.advanceTarget&&!options.offline?await target.runs.run(new AbortController().signal):{status:'observation-only',advanced:false};
   if(options.advanceTarget&&!options.offline&&result.status!=='completed')throw Error('BRANCH_RUN_'+result.status+': '+JSON.stringify(result));
   report={plan,receipt,result,sourcePoint:cut,targetPoint:await target.history.current(),authority:target.attempts.authority(),observation:await target.observation.query({sql:'SELECT id,attributes FROM s0_actors ORDER BY id'}),receipts:await target.observation.query({sql:'SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id'}),status:await target.observation.status(),history:target.history.diagnostics(),timings:{planMs,createMs},modelRequests:options.advanceTarget&&!options.offline&&model?null:0};
   if(uses('@society0/social/social'))report.socialObservation={posts:await target.observation.query({sql:'SELECT id,author_id,content FROM social_posts ORDER BY ordinal'}),deliveries:await target.observation.query({sql:'SELECT event_id,post,recipient,sender FROM social_deliveries ORDER BY ordinal'})};
   await writeFile(join(targetDirectory,'profile.json'),JSON.stringify(profile,null,2),{flag:'wx'});
  }finally{await consumer?.dispose();await lease.release();await handle.close()}
  report.closed=!host.ctx.runDirectory.list().some(run=>run.runId===targetRunId);
  return report;
 }});
}

async function main(){
 const {values:v}=parseArgs({options:{profile:{type:'string'},directory:{type:'string'},'run-id':{type:'string'},lock:{type:'string'},'lineage-directory':{type:'string'},lineage:{type:'string'},'target-profile':{type:'string'},'target-directory':{type:'string'},'target-run-id':{type:'string'},'advance-target':{type:'boolean'},offline:{type:'boolean'},help:{type:'boolean'}}});
 if(v.help){console.log('Usage: node run-profile-branch.mjs --profile <source-profile> --directory <existing-run> --run-id <source-id> --lock <lock> --target-profile <new-profile> --target-directory <new-run> --target-run-id <new-id> [--advance-target] [--offline]\nReopen and observe the source, plan and create a native full-profile branch, observe and close both runs. Preserve the emitted lineage directory and identity when reopening the target. Unknown operations require probe before retry.');return;}
 for(const key of ['profile','directory','run-id','target-profile','target-directory','target-run-id'])if(!v[key])throw Error('BRANCH_ARGUMENT: provide --'+key+'; use --help');
 console.log(JSON.stringify(await forkProfile({...v,runId:v['run-id'],lineageDirectory:v['lineage-directory'],targetProfile:v['target-profile'],targetDirectory:v['target-directory'],targetRunId:v['target-run-id'],advanceTarget:v['advance-target']})));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)await main().catch(error=>{console.error(error);process.exitCode=1});
