// cases: PR12-T01 PR12-T03 RV3-11-T02
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify,parseArgs} from 'node:util';
import {mkdir,readFile,writeFile,stat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
const exec=promisify(execFile);
const usage='Usage: node pr12-large-plan.mjs <installed-consumer> <new-evidence-dir> --timeout-ms <positive-safe-integer>';
export function argumentsFor(args){
 let parsed;try{parsed=parseArgs({args,allowPositionals:true,options:{'timeout-ms':{type:'string'}}})}catch(error){throw Error(usage,{cause:error})}
 const timeoutMs=Number(parsed.values['timeout-ms']);
 if(parsed.positionals.length!==2||!Number.isSafeInteger(timeoutMs)||timeoutMs<=0)throw Error(usage);
 return {consumer:resolve(parsed.positionals[0]),output:resolve(parsed.positionals[1]),timeoutMs};
}
export async function largeProfile(consumer){
 const profile=JSON.parse(await readFile(join(consumer,'profiles/rule-sqlite.json'),'utf8'));
 const run=profile.patches[0].insert.find(entry=>entry.id==='run');
 const bootstrap=run.config.find(entry=>entry.id==='bootstrap');
 bootstrap.config.unshift({id:'large-bootstrap-random',name:'@society0/core/random',config:{algorithm:'philox4x32-10/v1',seed:'literal-bootstrap-seed-'.repeat(60000)}});
 const initialActors=Array.from({length:1000},(_,i)=>({id:i===0?'alice':'actor-'+i,attributes:{body:'x'.repeat(128)}}));
 const proposals=[{id:'p0',planKind:'staged/1',payload:{phases:[{id:'act',activations:[{id:'a0',actorId:'alice',momentId:'m0',source:'agenda',driver:'rule',input:null}]}],nextCursor:'1'}}];
 profile.patches.push({id:'plain-init',config:{initialActors}},{id:'proposal-sequence',config:{proposals}});
 profile.lockfile=pathToFileURL(join(consumer,'module-lock.json')).href;
 const text=JSON.stringify(profile);assert(Buffer.byteLength(text)>1048576);return text;
}
export function sameProcessCode(source,target){
 return `const source=${JSON.stringify(source)},target=${JSON.stringify(target)};
const {runProfile}=await import('./run-profile.mjs');
const {forkProfile}=await import('./run-profile-branch.mjs');
const create=await runProfile(source);
const forked=await forkProfile({...source,...target});
console.log(JSON.stringify({create,forked,entry:{pid:process.pid,inputType:process.execArgv.filter(x=>x.startsWith('--input-type='))}}));`;
}
async function execute(options,args){
 const result=await exec(process.execPath,args,{cwd:options.consumer,timeout:options.timeoutMs,maxBuffer:32*1024*1024});
 assert.equal(result.stderr,'');return JSON.parse(result.stdout);
}
async function fileEntry(options,source,target){
 const common=['--profile',source.profile,'--directory',source.directory,'--run-id',source.runId,'--lock',source.lock];
 const create=await execute(options,['run-profile.mjs',...common]);
 const forked=await execute(options,['run-profile-branch.mjs',...common,'--target-profile',target.targetProfile,'--target-directory',target.targetDirectory,'--target-run-id',target.targetRunId]);
 return {create,forked,entry:{kind:'file'}};
}
async function evaluateEntry(options,source,target){
 const code=sameProcessCode(source,target);await writeFile(join(options.output,'eval-entry.mjs'),code,{flag:'wx'});
 const result=await execute(options,['--input-type=module','-e',code]);
 assert.deepEqual(result.entry.inputType,['--input-type=module']);return result;
}
async function verify(result,target){
 const {create,forked}=result;
 assert.equal(create.actorCount.integer,'1000');assert.equal(create.cut.frontier.completedCount,'1');
 assert.equal(create.close.drained,true);assert.equal(create.close.status,'closed');
 assert.equal(forked.close.drained,true);assert.equal(forked.close.status,'closed');assert.equal(forked.branch.closed,true);
 assert.equal(forked.branch.actorCount.integer,'1000');assert.equal(forked.branch.targetPoint.frontier.completedCount,'1');
 const planBytes=Buffer.byteLength(JSON.stringify(forked.branch.plan));assert(planBytes>1048576);
 const originPath=join(target.targetDirectory,'branch-origin.json'),originBytes=(await stat(originPath)).size;
 assert(originBytes<planBytes);const origin=JSON.parse(await readFile(originPath,'utf8'));
 assert.equal(origin.operationId,forked.branch.plan.operationId);
 const journal=JSON.parse(await readFile(origin.plan.journalPath,'utf8'));assert.deepEqual(journal.plan,forked.branch.plan);
 return {entry:result.entry,actorCount:forked.branch.actorCount,planBytes,originBytes,origin,sourcePoint:forked.branch.sourcePoint,targetPoint:forked.branch.targetPoint,close:forked.close,branchClosed:forked.branch.closed,metrics:{create:create.metrics,fork:forked.branch.timings},modelRequests:{create:create.modelRequests,fork:forked.modelRequests}};
}
export async function main(args){
 const options=argumentsFor(args),text=await largeProfile(options.consumer);await mkdir(options.output,{recursive:false});
 const profile=join(options.output,'large-profile.json');await writeFile(profile,text,{flag:'wx'});const results=[];
 for(const kind of ['file','eval']){
  const source={profile,directory:join(options.output,kind+'-source'),runId:'large-'+kind,lock:join(options.consumer,'module-lock.json')};
  const target={targetProfile:profile,targetDirectory:join(options.output,kind+'-target'),targetRunId:'large-'+kind+'-target'};
  const result=kind==='file'?await fileEntry(options,source,target):await evaluateEntry(options,source,target);
  results.push({kind,...await verify(result,target)});
  await writeFile(join(options.output,'results.json'),JSON.stringify({profileBytes:Buffer.byteLength(text),dimensions:{actors:1000,steps:1,bodyBytes:128},timeoutMs:options.timeoutMs,results},null,2));
 }
 console.log(JSON.stringify({status:'passed',results}));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)await main(process.argv.slice(2));
