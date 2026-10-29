import {Context,Service,type Fiber} from '@society0/cordis';
import Schema from '@society0/schemastery';
import {randomUUID} from 'node:crypto';
import {mkdir,open,readFile,rename} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import type {PluginMetadata,ApiEntry} from '../module-resolver/index.js';
import type {CompositionPlan} from '../profile-loader/index.js';
import {assertLiteral} from '../config-resolvers/types.js';
import {createBranch} from './create.js';
import type {BranchPlan,BranchProfile,BranchSourcePort,BranchReceipt,RandomSelection} from './ports.js';
import '../run-directory/index.js';import '../commands/index.js';import '../profile-loader/index.js';
export * from './ports.js';export {createBranch};
export const Config=Schema.object({directory:Schema.string().required(),onUnknown:Schema.const('halt').default('halt')});
export const metadata:PluginMetadata={title:'run-branch',summary:'Fixed-config branch plans with durable operation receipts and private preparation',configVersion:1,scope:'host',allowedScopes:['host'],hostAbi:'society0/host/1',configPolicy:[{path:'/directory',mode:'recreate',affectsResults:false},{path:'/onUnknown',mode:'new-run',affectsResults:true}]};
export const api:ApiEntry={exportPath:'./run-branch',provides:[{key:'runBranch',protocol:'runBranch',major:1,capabilities:[]}],requires:['runDirectory','commands','profileLoader'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module'}))};
interface Journal {plan:BranchPlan;status:'planned'|'creating'|'created'|'failed'|'unknown';summary?:BranchReceipt;reason?:string}
export type BranchProbe={status:'planned'|'creating'|'failed'|'unknown';reason?:string}|{status:'created';summary:BranchReceipt;live:boolean};
declare module '@society0/cordis' {interface Context {runBranch:RunBranch}}
export class RunBranch extends Service {
 static Config=Config;static inject=['runDirectory','commands','profileLoader'];private directory:string;private active=new Map<string,{plan:BranchPlan;task:Promise<BranchReceipt>}>();private closed=false;
 constructor(ctx:Context,input:ReturnType<typeof Config>){super(ctx,'runBranch');this.directory=resolve(input.directory);ctx.commands.register({name:'runBranch.plan',summary:'Plan a new run from an explicit complete point',scope:'host',input:(v:{runId:string;pointId:string;profile:BranchProfile})=>v,execute:(_ctx,input)=>this.plan(input.runId,input.pointId,input.profile)});ctx.commands.register({name:'runBranch.create',summary:'Create the exact recorded branch operation',scope:'host',input:(v:BranchPlan)=>v,execute:(_ctx,input)=>this.create(input)});ctx.commands.register({name:'runBranch.probe',summary:'Probe a branch operation without resending it',scope:'host',input:(id:unknown)=>{if(typeof id!=='string')throw Error('BRANCH_OPERATION_ID');return id},execute:(_ctx,id)=>this.probe(id)});ctx.effect(()=>async()=>{this.closed=true;await Promise.allSettled([...this.active.values()].map(item=>item.task))})}
 private path(id:string){if(!/^[a-zA-Z0-9_-]+$/.test(id))throw Error('BRANCH_OPERATION_ID');return join(this.directory,id+'.json')}
 private async read(id:string):Promise<Journal>{return JSON.parse(await readFile(this.path(id),'utf8'))}
 private async save(record:Journal,initial=false){await mkdir(this.directory,{recursive:true});const path=this.path(record.plan.operationId);const next=initial?path:path+'.next';const file=await open(next,'wx');try{await file.writeFile(JSON.stringify(record));await file.sync()}finally{await file.close()}if(!initial)await rename(next,path);const directory=await open(this.directory,'r');try{await directory.sync()}finally{await directory.close()}}
 private randomSelection(plan:CompositionPlan):RandomSelection|undefined{
  const entries=plan.flat.filter(entry=>!entry.disabled&&entry.scope==='generation'&&entry.binding?.api.provides.some(service=>service.key==='random'));
  if(entries.length>1)throw Error('BRANCH_RANDOM_AMBIGUOUS');if(!entries.length)return;
  return randomConfig(entries[0]!.config);
 }

 private generationEvidence(plan:CompositionPlan){return JSON.parse(JSON.stringify({plan:this.ctx.profileLoader.dump(plan),secrets:plan.flat.map(entry=>({id:entry.id,versions:entry.evidence.secretVersions})),intercepts:plan.flat.map(entry=>({id:entry.id,intercept:entry.intercept}))}))}
 private async borrow(runId:string){const lease=this.ctx.runDirectory.acquire(runId,'read');let consumer:Fiber|undefined;let source:BranchSourcePort|undefined;try{consumer=await lease.ctx.plugin({name:'branch.borrow',inject:['branchSource'],apply(ctx){source=Reflect.get(ctx,'branchSource') as BranchSourcePort}});if(!source)throw Error('BRANCH_SOURCE_UNAVAILABLE');return {source,generationPlan:lease.generationPlan,release:async()=>{try{await consumer!.dispose()}finally{await lease.release()}}}}catch(error){try{await consumer?.dispose()}finally{await lease.release()}throw error}}
 async plan(runId:string,pointId:string,profile:BranchProfile):Promise<BranchPlan>{
  if(this.closed)throw Error('BRANCH_CLOSED');validateProfile(runId,profile);
  const held=await this.borrow(runId);try{const point=await held.source.describe(pointId);if(point.id!==pointId||point.runId!==runId)throw Error('BRANCH_POINT_IDENTITY');const generation=await this.ctx.profileLoader.compile(profile.profile,{baseURL:profile.baseURL});
   const sourceRandom=this.randomSelection(held.generationPlan),targetRandom=this.randomSelection(generation);validateSeed(profile,sourceRandom,targetRandom);
   const plan=JSON.parse(JSON.stringify({sourceRandom,targetRandom,format:'society0/branch-plan/1',operationId:randomUUID(),sourceRunId:runId,point:structuredClone(point),target:structuredClone({...profile,targetDirectory:resolve(profile.targetDirectory)}),sources:planSources(generation),generationEvidence:this.generationEvidence(generation),random:{algorithm:'philox4x32-10/v1',mapping:'length-prefixed-utf8-seed-seq/v1',seedPolicy:profile.seedPolicy}})) as BranchPlan;await this.save({plan,status:'planned'},true);return plan;
  }finally{await held.release()}
 }
 /** Existing runs keep their result-related configuration fixed. */
 assessChange(current:unknown,proposed:unknown,metadata?:PluginMetadata){
  const changed=!isDeepStrictEqual(current,proposed);
  const read=(value:unknown,path:string)=>path.split('/').slice(1).reduce<unknown>((item,key)=>item!==null&&typeof item==='object'?Reflect.get(item,key.replace(/~1/g,'/').replace(/~0/g,'~')):undefined,value);
  const fields=(metadata?.configPolicy??[]).filter(policy=>!isDeepStrictEqual(read(current,policy.path),read(proposed,policy.path))).map(policy=>({path:policy.path,declaredMode:policy.mode,affectsResults:policy.affectsResults}));
  return {changed,requirement:changed?'new-run' as const:'unchanged' as const,fields,reason:changed?'Current run profiles are fixed; changes require an explicit branch plan.':undefined};
 }
 create(plan:BranchPlan):Promise<BranchReceipt>{if(this.closed)return Promise.reject(Error('BRANCH_CLOSED'));const existing=this.active.get(plan.operationId);if(existing)return isDeepStrictEqual(existing.plan,plan)?existing.task:Promise.reject(Error('BRANCH_PLAN_CHANGED'));const snapshot=structuredClone(plan);const task=this.perform(snapshot);this.active.set(plan.operationId,{plan:snapshot,task});void task.then(()=>this.active.delete(plan.operationId),()=>this.active.delete(plan.operationId));return task}
 private verifyGeneration(plan:BranchPlan,generation:CompositionPlan){
  if(!isDeepStrictEqual(planSources(generation),plan.sources)||!isDeepStrictEqual(this.generationEvidence(generation),plan.generationEvidence))throw Error('BRANCH_SOURCE_CHANGED');
 }
 private async failedPreparation(record:Journal,source:BranchSourcePort,error:unknown){
  const probe=await source.probe(record.plan).catch(reason=>({status:'unknown' as const,reason:String(reason)}));
  await this.save({...record,status:probe.status==='not-prepared'?'failed':'unknown',reason:String(error)});
 }
 private async perform(plan:BranchPlan):Promise<BranchReceipt>{
  const record=await this.read(plan.operationId);if(!isDeepStrictEqual(plan,record.plan))throw Error('BRANCH_PLAN_CHANGED');if(record.status==='created')return record.summary!;if(record.status!=='planned')throw Error('BRANCH_'+record.status.toUpperCase()+' probe '+plan.operationId);
  const held=await this.borrow(plan.sourceRunId);let generation:CompositionPlan;let created:BranchReceipt|undefined;
  try{
   generation=await this.ctx.profileLoader.compile(plan.target.profile,{baseURL:plan.target.baseURL});this.verifyGeneration(plan,generation);
   await this.save({...record,status:'creating'});
   const {handle,point}=await createBranch(this.ctx,plan,generation,held.source,{journalPath:this.path(plan.operationId),operationId:plan.operationId});const summary:BranchReceipt={...this.ctx.runDirectory.list().find(x=>x.runId===handle.runId)!,point,generation:plan.target.generation};created=summary;await this.save({...record,status:'created',summary});return summary;
  }catch(error){if(created){await this.save({...record,status:'created',summary:created});return created}await this.failedPreparation(record,held.source,error);throw error}
  finally{await held.release()}
 }
 async probe(operationId:string):Promise<BranchProbe>{const record=await this.read(operationId);if(record.status==='created')return {status:'created',summary:record.summary!,live:this.ctx.runDirectory.list().some(run=>run.runId===record.summary!.runId&&run.rootId===record.summary!.point.id&&!run.closing)};if(this.active.has(operationId))return {status:'creating'};return {status:record.status==='creating'?'unknown':record.status,reason:record.reason}}
}
function randomRecord(config:unknown):object{
 if(!config||typeof config!=='object'||Array.isArray(config))throw Error('BRANCH_RANDOM_CONFIG');
 return config;
}
function randomConfig(config:unknown):RandomSelection{
 const record=randomRecord(config);
 const algorithm=Reflect.get(record,'algorithm'),seed=Reflect.get(record,'seed');
 if(algorithm!=='philox4x32-10/v1'||typeof seed!=='string')throw Error('BRANCH_RANDOM_CONFIG');
 return {algorithm:'philox4x32-10/v1',mapping:'length-prefixed-utf8-seed-seq/v1',seed};
}
function validateProfile(runId:string,profile:BranchProfile){
 assertLiteral(profile as unknown);
 if(!profile.mount?.runEntryId||!profile.mount.bootstrapEntryId)throw Error('BRANCH_PHASE_REQUIRED');
 if(!['preserve-seed-new-run-address','explicit-seed-new-run-address'].includes(profile.seedPolicy))throw Error('BRANCH_SEED_POLICY');
 validateTarget(runId,profile);
}
function validateTarget(runId:string,profile:BranchProfile){
 if(!profile.targetRunId||profile.targetRunId===runId||!profile.targetDirectory||!profile.generation||!profile.controlSequence)throw Error('BRANCH_TARGET');
}
function validateSeed(profile:BranchProfile,source:RandomSelection|undefined,target:RandomSelection|undefined){
 if(profile.seedPolicy==='preserve-seed-new-run-address'&&!isDeepStrictEqual(source,target))throw Error('BRANCH_SEED_POLICY_MISMATCH');
}
function planSources(plan:CompositionPlan){return plan.flat.flatMap(entry=>entry.binding?.identity?[entry.binding.identity.source]:[])}
export default RunBranch;
