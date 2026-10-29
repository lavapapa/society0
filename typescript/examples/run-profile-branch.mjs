#!/usr/bin/env node
import {readFile,writeFile,open} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {setTimeout as delay} from 'node:timers/promises';
import {applyEntryPatches} from '@society0/include';
import {runProfile} from './run-profile.mjs';

export async function forkProfile(options){
 const branch=await readTarget(options);requireTargetEvents(branch);
 return runProfile({...options,reopen:true,observe:true,branchDirectory:options.branchDirectory??join(dirname(branch.targetDirectory),'branch-operations'),afterRun:source=>executeBranch(branch,source)});
}
function literalEntries(entries){return entries.flatMap(entry=>entry.group?literalEntries(entry.config??[]):[entry]);}
function dumpEntries(entries){return entries.flatMap(entry=>[entry,...dumpEntries(entry.children??[])]);}
function targetConfig(branch,id){return branch.entries.find(entry=>entry.id===id)?.config??{};}
async function readTarget(options){
 const targetDirectory=resolve(options.targetDirectory),targetRunId=options.targetRunId,targetURL=pathToFileURL(resolve(options.targetProfile));
 const profile=JSON.parse(await readFile(targetURL,'utf8'));if(options.lock)profile.lockfile=pathToFileURL(resolve(options.lock)).href;
 const entries=literalEntries(applyEntryPatches([],profile.patches,message=>{throw Error('PROFILE_PATCH: '+message)}));
 const eventTarget=entries.some(entry=>!entry.disabled&&entry.name==='@society0/core/proposal-events');
 return {options,targetDirectory,targetRunId,targetURL,profile,entries,eventTarget,advance:options.advanceTarget&&!options.offline};
}
function requireTargetEvents(branch){
 if(!branch.eventTarget||!branch.advance)return;
 if(!Array.isArray(branch.options.targetEvents)||!branch.options.targetEvents.length)throw Error('TARGET_EVENTS_REQUIRED: event target advance needs a nonempty targetEvents batch (--target-events <json-file>); the example ends the source after this batch');
}
function validateTargetFrontier(branch){
 if(!branch.eventTarget||!branch.advance)return;
 let offset=BigInt(branch.sourcePoint.frontier.value),time=BigInt(branch.sourcePoint.frontier.logicalTime.time);
 for(const event of branch.options.targetEvents){
  if(BigInt(event.offset)<=offset||BigInt(event.time)<time)throw Error('TARGET_EVENT_FRONTIER: '+event.id+' must follow offset '+offset+' and moment '+time);
  offset=BigInt(event.offset);time=BigInt(event.time);
 }
}
function applyTargetAddress(branch,authority){
 branch.generation=branch.options.generation??'g2';branch.controlSequence=String(BigInt(branch.sourcePoint.controlSequence)+1n);
 const {targetDirectory,targetRunId,generation,controlSequence}=branch;
 branch.profile.patches.push(
  {id:'sqlite',config:{...targetConfig(branch,'sqlite'),directory:targetDirectory,runId:targetRunId,generation}},
  {id:'artifacts-local',config:{...targetConfig(branch,'artifacts-local'),directory:join(targetDirectory,'artifacts')}},
  {id:'sqlite-history',config:{...targetConfig(branch,'sqlite-history'),directory:targetDirectory,runId:targetRunId,generation,controlSequence}},
  {id:'attempts',config:{...targetConfig(branch,'attempts'),directory:authority.directory,lineage:authority.lineage}},
  {id:'generation-binding',config:{...targetConfig(branch,'generation-binding'),generation,controlSequence}}
 );
}
async function planBranch(branch){
 const started=performance.now(),{targetRunId,targetDirectory,generation,controlSequence,profile}=branch;
 branch.plan=await branch.host.ctx.runBranch.plan(branch.options.runId,branch.sourcePoint.id,{targetRunId,targetDirectory,generation,controlSequence,mount:{runEntryId:'run',bootstrapEntryId:'bootstrap'},profile,baseURL:branch.targetURL.href,migrations:[],seedPolicy:'preserve-seed-new-run-address'});
 branch.active=dumpEntries(branch.plan.generationEvidence.plan.entries).filter(entry=>!entry.disabled);
 branch.model=targetUses(branch,'@society0/core/driver-llm');authorizeTarget(branch);branch.planMs=performance.now()-started;
}
function targetUses(branch,name){return branch.active.some(entry=>entry.name===name);}
function authorizeTarget(branch){
 if(!branch.advance)return;
 if(targetUses(branch,'@society0/core/model-codex-subscription'))throw Error('CODEX_NOT_AUTHORIZED: target remains planned; subscription execution requires separate authorization');
 if(branch.model&&!branch.options.allowModelRequests)throw Error('MODEL_NOT_AUTHORIZED: target remains planned; obtain explicit model authorization before execution');
}
async function createBranch(branch){
 const started=performance.now(),stop=branch.options.diagnosticsPath?await monitorHistory(branch.sourceCtx.history,branch.options.diagnosticsPath,branch.plan.operationId):undefined;
 try{branch.receipt=await branch.host.ctx.runBranch.create(branch.plan)}
 catch(error){throw Error('BRANCH_CREATE_UNRESOLVED: '+JSON.stringify({operationId:branch.plan.operationId,sourceRunId:branch.options.runId,sourcePointId:branch.sourcePoint.id,targetDirectory:branch.targetDirectory})+'; call runBranch.probe(operationId) before retry',{cause:error})}
 finally{await stop?.()}
 branch.createMs=performance.now()-started;
}
async function executeBranch(branch,{host,ctx,cut}){
 branch.host=host;branch.sourceCtx=ctx;branch.sourcePoint=branch.options.pointId?await ctx.history.describe(branch.options.pointId):cut;
 validateTargetFrontier(branch);applyTargetAddress(branch,ctx.attempts.authority());await planBranch(branch);await createBranch(branch);
 const report=await consumeTarget(branch);report.closed=!host.ctx.runDirectory.list().some(run=>run.runId===branch.targetRunId);return report;
}
async function consumeTarget(branch){
 const lease=branch.host.ctx.runDirectory.acquire(branch.targetRunId,branch.advance?'write':'read'),handle=lease.value;let consumer;
 try{
  let target;consumer=await lease.ctx.plugin({name:'public.branch.consume',inject:['runs','observation','history','attempts','generationViews'],apply(ctx){target=ctx}});await consumer.await();
  await feedTargetEvents(branch,target);const result=await advanceTarget(branch,target),report=await observeTarget(branch,target,result);
  await writeFile(join(branch.targetDirectory,'profile.json'),JSON.stringify(branch.profile,null,2),{flag:'wx'});return report;
 }finally{await consumer?.dispose();await lease.release();await handle.close()}
}
async function feedTargetEvents(branch,target){
 if(!branch.eventTarget||!branch.advance)return;
 const held=await target.generationViews.acquire(branch.generation);
 try{try{for(const event of branch.options.targetEvents)await held.value.source.push(event)}finally{held.value.source.end()}}finally{await held.release()}
}
async function advanceTarget(branch,target){
 if(!branch.advance)return {status:'observation-only',advanced:false};
 const result=await target.runs.run(new AbortController().signal);
 if(result.status!=='completed')throw Error('BRANCH_RUN_'+result.status+': '+JSON.stringify(result));return result;
}
async function observeTarget(branch,target,result){
 const {plan,receipt,sourcePoint,planMs,createMs}=branch;
 const report={plan,receipt,result,sourcePoint,threadCount:(await target.observation.query({sql:'SELECT COUNT(*) AS count FROM threads'})).rows[0].count,targetPoint:await target.history.current(),authority:target.attempts.authority(),actorCount:(await target.observation.query({sql:'SELECT COUNT(*) AS count FROM s0_actors'})).rows[0].count,observation:await target.observation.query({sql:'SELECT id,attributes FROM s0_actors ORDER BY id'}),receipts:await target.observation.query({sql:'SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id'}),status:await target.observation.status(),history:target.history.diagnostics(),timings:{planMs,createMs},modelRequests:branch.advance&&branch.model?null:0};
 if(targetUses(branch,'@society0/social/social'))report.socialObservation={posts:await target.observation.query({sql:'SELECT id,author_id,content FROM social_posts ORDER BY ordinal'}),deliveries:await target.observation.query({sql:'SELECT event_id,post,recipient,sender FROM social_deliveries ORDER BY ordinal'})};
 return report;
}
function counterDelta(current,baseline){
 const counters={};for(const key of ['applyCount','backupBytes','rootCopies','segmentsSealed','compressedInputBytes','compressedOutputBytes','objectCopies','objectHardlinks','pointRecordsRead'])counters[key]=(current[key]??0)-(baseline[key]??0);return counters;
}
function timingDelta(value,baseline={}){return {count:value.count-(baseline.count??0),failed:value.failed-(baseline.failed??0),totalMs:value.totalMs-(baseline.totalMs??0)};}
function historyTimings(current,baseline){return Object.fromEntries(Object.entries(current.timings??{}).map(([key,value])=>[key,timingDelta(value,baseline.timings?.[key])]));}
async function sampleHistory(monitor,final=false){
 const before=performance.now(),current=monitor.history.diagnostics(),counters=counterDelta(current,monitor.baseline),timings=historyTimings(current,monitor.baseline);
 await monitor.file.write(JSON.stringify({operationId:monitor.operationId,elapsedMs:before-monitor.started,final,active:current.active,counters,timings,lastNative:current.lastNative,memory:process.memoryUsage(),previousSamplingMs:monitor.overheadMs,sample:monitor.samples++})+'\n');monitor.overheadMs+=performance.now()-before;
}
async function sampleUntilStopped(monitor){
 while(!monitor.stop.signal.aborted){
  try{await delay(5000,undefined,{signal:monitor.stop.signal})}catch(error){if(monitor.stop.signal.aborted)break;throw error}
  await sampleHistory(monitor);
 }
}
async function monitorHistory(history,path,operationId){
 const file=await open(resolve(path),'wx'),monitor={file,history,operationId,baseline:history.diagnostics(),started:performance.now(),stop:new AbortController(),overheadMs:0,samples:0};
 await sampleHistory(monitor);const loop=sampleUntilStopped(monitor);void loop.catch(()=>{});
 return async()=>{monitor.stop.abort();try{await loop;await sampleHistory(monitor,true)}finally{await file.close()}};
}

async function main(){
 const {values:v}=parseArgs({options:{profile:{type:'string'},directory:{type:'string'},'run-id':{type:'string'},lock:{type:'string'},'lineage-directory':{type:'string'},lineage:{type:'string'},'target-profile':{type:'string'},'target-directory':{type:'string'},'target-run-id':{type:'string'},'target-events':{type:'string'},diagnostics:{type:'string'},'point-id':{type:'string'},'advance-target':{type:'boolean'},offline:{type:'boolean'},help:{type:'boolean'}}});
 if(v.help){console.log('Usage: node run-profile-branch.mjs --profile <source-profile> --directory <existing-run> --run-id <source-id> --lock <lock> --target-profile <new-profile> --target-directory <new-run> --target-run-id <new-id> [--lineage-directory <path>] [--lineage <id>] [--point-id <complete-point-id>] [--diagnostics <new-ndjson-file>] [--advance-target [--target-events <json-file>]] [--offline]\nReopen and observe the source, plan and create a native full-profile branch, observe and close both runs. Preserve the emitted lineage directory and identity when reopening the target. Event target advance requires a nonempty JSON array of new events with offsets after the selected point and nondecreasing moments; the queue is ended after the batch. Unknown operations require probe before retry.');return;}
 for(const key of ['profile','directory','run-id','target-profile','target-directory','target-run-id'])if(!v[key])throw Error('BRANCH_ARGUMENT: provide --'+key+'; use --help');
 console.log(JSON.stringify(await forkProfile({...v,runId:v['run-id'],lineageDirectory:v['lineage-directory'],targetProfile:v['target-profile'],targetDirectory:v['target-directory'],targetRunId:v['target-run-id'],diagnosticsPath:v.diagnostics,pointId:v['point-id'],advanceTarget:v['advance-target'],targetEvents:v['target-events']?JSON.parse(await readFile(resolve(v['target-events']),'utf8')):undefined})));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)await main().catch(error=>{console.error(error);process.exitCode=1});
