#!/usr/bin/env node
import {readFile,writeFile,open} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {setTimeout as delay} from 'node:timers/promises';
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
 const eventTarget=entries.some(entry=>!entry.disabled&&entry.name==='@society0/core/proposal-events'),advance=options.advanceTarget&&!options.offline;
 if(eventTarget&&advance&&(!Array.isArray(options.targetEvents)||!options.targetEvents.length))throw Error('TARGET_EVENTS_REQUIRED: event target advance needs a nonempty targetEvents batch (--target-events <json-file>); the example ends the source after this batch');
 return runProfile({...options,reopen:true,observe:true,branchDirectory:options.branchDirectory??join(dirname(targetDirectory),'branch-operations'),afterRun:async({host,ctx,cut})=>{
  const sourcePoint=options.pointId?await ctx.history.describe(options.pointId):cut;
   if(eventTarget&&advance){let offset=BigInt(sourcePoint.frontier.value),time=BigInt(sourcePoint.frontier.logicalTime.time);for(const event of options.targetEvents){if(BigInt(event.offset)<=offset||BigInt(event.time)<time)throw Error('TARGET_EVENT_FRONTIER: '+event.id+' must follow offset '+offset+' and moment '+time);offset=BigInt(event.offset);time=BigInt(event.time);}}
  const generation=options.generation??'g2',controlSequence=String(BigInt(sourcePoint.controlSequence)+1n),authority=ctx.attempts.authority();
  profile.patches.push(
   {id:'sqlite',config:{...config('sqlite'),directory:targetDirectory,runId:targetRunId,generation}},
   {id:'artifacts-local',config:{...config('artifacts-local'),directory:join(targetDirectory,'artifacts')}},
   {id:'sqlite-history',config:{...config('sqlite-history'),directory:targetDirectory,runId:targetRunId,generation,controlSequence}},
   {id:'attempts',config:{...config('attempts'),directory:authority.directory,lineage:authority.lineage}},
   {id:'generation-binding',config:{...config('generation-binding'),generation,controlSequence}}
  );
  const started=performance.now();
  const plan=await host.ctx.runBranch.plan(options.runId,sourcePoint.id,{targetRunId,targetDirectory,generation,controlSequence,mount:{runEntryId:'run',bootstrapEntryId:'bootstrap'},profile,baseURL:targetURL.href,migrations:[],seedPolicy:'preserve-seed-new-run-address'});
  const walk=entries=>entries.flatMap(entry=>[entry,...walk(entry.children??[])]);
  const active=walk(plan.generationEvidence.plan.entries).filter(entry=>!entry.disabled);
  const uses=name=>active.some(entry=>entry.name===name),model=uses('@society0/core/driver-llm');
  if(options.advanceTarget&&!options.offline&&uses('@society0/core/model-codex-subscription'))throw Error('CODEX_NOT_AUTHORIZED: target remains planned; subscription execution requires separate authorization');
  if(options.advanceTarget&&!options.offline&&model&&!options.allowModelRequests)throw Error('MODEL_NOT_AUTHORIZED: target remains planned; obtain explicit model authorization before execution');
  const planMs=performance.now()-started,creating=performance.now();
  const stopDiagnostics=options.diagnosticsPath?await monitorHistory(ctx.history,options.diagnosticsPath,plan.operationId):undefined;
   let receipt;try{receipt=await host.ctx.runBranch.create(plan)}catch(error){throw Error('BRANCH_CREATE_UNRESOLVED: '+JSON.stringify({operationId:plan.operationId,sourceRunId:options.runId,sourcePointId:sourcePoint.id,targetDirectory})+'; call runBranch.probe(operationId) before retry',{cause:error})}finally{await stopDiagnostics?.()}const createMs=performance.now()-creating;
  const lease=host.ctx.runDirectory.acquire(targetRunId,options.advanceTarget&&!options.offline?'write':'read'),handle=lease.value;
  let report,consumer;
  try{
   let target;consumer=await lease.ctx.plugin({name:'public.branch.consume',inject:['runs','observation','history','attempts','generationViews'],apply(ctx){target=ctx}});await consumer.await();
   if(eventTarget&&advance){const held=await target.generationViews.acquire(generation);try{try{for(const event of options.targetEvents)await held.value.source.push(event)}finally{held.value.source.end()}}finally{await held.release()}}
    const result=options.advanceTarget&&!options.offline?await target.runs.run(new AbortController().signal):{status:'observation-only',advanced:false};
   if(options.advanceTarget&&!options.offline&&result.status!=='completed')throw Error('BRANCH_RUN_'+result.status+': '+JSON.stringify(result));
   report={plan,receipt,result,sourcePoint,threadCount:(await target.observation.query({sql:'SELECT COUNT(*) AS count FROM threads'})).rows[0].count,targetPoint:await target.history.current(),authority:target.attempts.authority(),actorCount:(await target.observation.query({sql:'SELECT COUNT(*) AS count FROM s0_actors'})).rows[0].count,observation:await target.observation.query({sql:'SELECT id,attributes FROM s0_actors ORDER BY id'}),receipts:await target.observation.query({sql:'SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id'}),status:await target.observation.status(),history:target.history.diagnostics(),timings:{planMs,createMs},modelRequests:options.advanceTarget&&!options.offline&&model?null:0};
   if(uses('@society0/social/social'))report.socialObservation={posts:await target.observation.query({sql:'SELECT id,author_id,content FROM social_posts ORDER BY ordinal'}),deliveries:await target.observation.query({sql:'SELECT event_id,post,recipient,sender FROM social_deliveries ORDER BY ordinal'})};
   await writeFile(join(targetDirectory,'profile.json'),JSON.stringify(profile,null,2),{flag:'wx'});
  }finally{await consumer?.dispose();await lease.release();await handle.close()}
  report.closed=!host.ctx.runDirectory.list().some(run=>run.runId===targetRunId);
  return report;
 }});
}

async function monitorHistory(history,path,operationId){
 const file=await open(resolve(path),'wx'),baseline=history.diagnostics(),started=performance.now(),stop=new AbortController();let overheadMs=0,samples=0;
 const sample=async(final=false)=>{
  const before=performance.now(),current=history.diagnostics(),counters={};
  for(const key of ['applyCount','backupBytes','rootCopies','segmentsSealed','compressedInputBytes','compressedOutputBytes','objectCopies','objectHardlinks','pointRecordsRead'])counters[key]=(current[key]??0)-(baseline[key]??0);
  const timings=Object.fromEntries(Object.entries(current.timings??{}).map(([key,value])=>[key,Object.fromEntries(['count','failed','totalMs'].map(field=>[field,value[field]-(baseline.timings?.[key]?.[field]??0)]))]));
  await file.write(JSON.stringify({operationId,elapsedMs:before-started,final,active:current.active,counters,timings,lastNative:current.lastNative,memory:process.memoryUsage(),previousSamplingMs:overheadMs,sample:samples++})+'\n');overheadMs+=performance.now()-before;
 };
 await sample();
 const loop=(async()=>{while(!stop.signal.aborted){try{await delay(5000,undefined,{signal:stop.signal})}catch(error){if(stop.signal.aborted)break;throw error}await sample()}})();void loop.catch(()=>{});
 return async()=>{stop.abort();try{await loop;await sample(true)}finally{await file.close()}};
}

async function main(){
 const {values:v}=parseArgs({options:{profile:{type:'string'},directory:{type:'string'},'run-id':{type:'string'},lock:{type:'string'},'lineage-directory':{type:'string'},lineage:{type:'string'},'target-profile':{type:'string'},'target-directory':{type:'string'},'target-run-id':{type:'string'},'target-events':{type:'string'},diagnostics:{type:'string'},'point-id':{type:'string'},'advance-target':{type:'boolean'},offline:{type:'boolean'},help:{type:'boolean'}}});
 if(v.help){console.log('Usage: node run-profile-branch.mjs --profile <source-profile> --directory <existing-run> --run-id <source-id> --lock <lock> --target-profile <new-profile> --target-directory <new-run> --target-run-id <new-id> [--lineage-directory <path>] [--lineage <id>] [--point-id <complete-point-id>] [--diagnostics <new-ndjson-file>] [--advance-target [--target-events <json-file>]] [--offline]\nReopen and observe the source, plan and create a native full-profile branch, observe and close both runs. Preserve the emitted lineage directory and identity when reopening the target. Event target advance requires a nonempty JSON array of new events with offsets after the selected point and nondecreasing moments; the queue is ended after the batch. Unknown operations require probe before retry.');return;}
 for(const key of ['profile','directory','run-id','target-profile','target-directory','target-run-id'])if(!v[key])throw Error('BRANCH_ARGUMENT: provide --'+key+'; use --help');
 console.log(JSON.stringify(await forkProfile({...v,runId:v['run-id'],lineageDirectory:v['lineage-directory'],targetProfile:v['target-profile'],targetDirectory:v['target-directory'],targetRunId:v['target-run-id'],diagnosticsPath:v.diagnostics,pointId:v['point-id'],advanceTarget:v['advance-target'],targetEvents:v['target-events']?JSON.parse(await readFile(resolve(v['target-events']),'utf8')):undefined})));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)await main().catch(error=>{console.error(error);process.exitCode=1});
