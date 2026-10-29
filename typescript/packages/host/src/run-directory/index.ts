import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '../module-resolver/index.js';
export const Config=Schema.object({maxOpenRuns:Schema.natural().min(1).default(16)});
export const metadata:PluginMetadata={"title":"run-directory","summary":"Host-owned unpublished preparation and leased run handles","configVersion":1,"configPolicy":[{"path":"/maxOpenRuns","mode":"recreate","affectsResults":true,"secret":false}],"scope":"host","allowedScopes":["host"],"hostAbi":"society0/host/1"};
export const api:ApiEntry={"exportPath":"./run-directory","provides":[{"key":"runDirectory","protocol":"runDirectory","major":1,"capabilities":[]}],"requires":[{"service":{"key":"profileLoader","protocol":"profileLoader","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"resources","protocol":"resources","major":1,"capabilities":[]},"optional":false,"phase":"module"}]};
import {Context,Service,type Fiber} from '@society0/cordis';
import '../resources/index.js';
import {type CompositionPlan,type MountedTree,type MountSession} from '../profile-loader/index.js';
export interface RunRoot {readonly id:string;readonly runId:string;readonly revision:string;readonly kind:'root'|'advance'}
export interface PreparedRun {readonly root:RunRoot;readonly metadata:unknown;readonly database:{readonly metadata:unknown};readonly state:'clean'|'dirty'|'unknown'}
export interface RunDirectoryPlan {
 readonly runId:string;
 /** Installs private SQL/bootstrap subtree; returns only after authoritative root probe. */
 prepare(ctx:Context,phase?:RunPreparationPhase):Promise<PreparedRun>;
 /** 完整IR分阶段挂载；节点ID显式定位实际隔离group。 */
 readonly mount?:{readonly runEntryId:string;readonly bootstrapEntryId:string};
 readonly generation:CompositionPlan;
 readonly isolateServices?:readonly string[];
 /** Explicit new-lineage authority; reopening and forks select existing or omit this hook. */
 readonly lineage?:{readonly kind:'fresh';initialize(ctx:Context):Promise<void>}|{readonly kind:'existing'};
}
export interface RunPreparationPhase {readonly run:Context;readonly bootstrap:Context;readonly tree:MountedTree}
export interface RunHandle {readonly runId:string;readonly root:RunRoot;readonly metadata:unknown;readonly database:PreparedRun['database'];readonly state:PreparedRun['state'];close():Promise<void>}
/** 原生 run scope 的借用入口；调用者在释放 lease 前结算临时消费者。 */
export interface RunLease {readonly value:RunHandle;readonly ctx:Context;readonly generationPlan:CompositionPlan;close():Promise<void>;release():Promise<void>}
export interface RunSummary {readonly runId:string;readonly state:PreparedRun['state'];readonly rootId:string;readonly leases:number;readonly closing:boolean}
interface Entry {owner:Fiber;runContext:Context;session?:MountSession;generationPlan:CompositionPlan;handle:RunHandle;leases:number;closing:boolean;empty:ReturnType<typeof Promise.withResolvers<void>>;close?:Promise<void>}
declare module '@society0/cordis' {interface Context {runDirectory:RunDirectory}}
export class RunDirectory extends Service {
 static Config=Config;
 static inject=['profileLoader','resources'];private entries=new Map<string,Entry>();private preparing=new Set<string>();private limit:number;private scope:()=>Context;
 constructor(ctx:Context,config:{maxOpenRuns?:number}={}){super(ctx,'runDirectory');this.scope=()=>ctx;this.limit=config.maxOpenRuns??16;if(!Number.isSafeInteger(this.limit)||this.limit<1)throw Error('RUN_CAPACITY');ctx.resources.use('runDirectory',async()=>{await Promise.all([...this.entries.values()].map(entry=>this.closeEntry(entry)))})}
 private admit(plan:RunDirectoryPlan){if(typeof plan.runId!=='string'||!plan.runId)throw Error('RUN_ID');if(this.entries.has(plan.runId)||this.preparing.has(plan.runId))throw Error('RUN_EXISTS');if(this.entries.size+this.preparing.size>=this.limit)throw Error('RUN_CAPACITY');this.preparing.add(plan.runId)}
 private async privateOwner(plan:RunDirectoryPlan){const keys=new Set(['database','executionFence','initializers','sqliteBootstrap','artifacts','history','slots',...plan.isolateServices??[],...plan.generation.flat.filter(entry=>entry.scope!=='host').flatMap(entry=>entry.binding?.api.provides.map(service=>service.key)??[])]);let scope=this.scope();for(const key of keys)scope=scope.isolate(key,Symbol(plan.runId+':'+key));return scope.plugin({name:'run:'+plan.runId,apply(){}})}
 private group(plan:CompositionPlan,tree:MountedTree,id:string,scope:'run'|'bootstrap'){const node=plan.flat.find(entry=>entry.id===id),fiber=tree.entries.get(id);if(!node?.group||node.scope!==scope||!fiber)throw Error('RUN_PHASE_GROUP '+id);return fiber.ctx}
 private async preparation(plan:RunDirectoryPlan,owner:Fiber,session?:MountSession){if(!session)return {prepared:await plan.prepare(owner.ctx),runContext:owner.ctx};const tree=await session.mountScopes(['run','bootstrap']);const run=this.group(plan.generation,tree,plan.mount!.runEntryId,'run'),bootstrap=this.group(plan.generation,tree,plan.mount!.bootstrapEntryId,'bootstrap');const prepared=await plan.prepare(bootstrap,{run,bootstrap,tree});await session.disposeScopes(['bootstrap']);return {prepared,runContext:run}}
 private publish(plan:RunDirectoryPlan,owner:Fiber,runContext:Context,prepared:PreparedRun,session?:MountSession){let entry:Entry;const handle:RunHandle=Object.freeze({runId:plan.runId,root:prepared.root,metadata:prepared.metadata,database:prepared.database,state:prepared.state,close:()=>this.closeEntry(entry)});entry={owner,runContext,session,generationPlan:plan.generation,handle,leases:0,closing:false,empty:Promise.withResolvers<void>()};this.entries.set(plan.runId,entry);return handle}
  private assertPrepared(plan:RunDirectoryPlan,prepared:PreparedRun):void{if(prepared.root.runId!==plan.runId||!prepared.root.id)throw Error('RUN_ROOT_IDENTITY');if(prepared.metadata!==prepared.database.metadata)throw Error('RUN_METADATA_IDENTITY');}
  private async mountGeneration(plan:RunDirectoryPlan,owner:Fiber,session?:MountSession):Promise<void>{if(session)await session.mountScopes(['generation']);else await this.ctx.profileLoader.mount(plan.generation,owner.ctx)}
 async create(plan:RunDirectoryPlan):Promise<RunHandle>{
  this.admit(plan);let owner:Fiber|undefined;let session:MountSession|undefined;
  try{owner=await this.privateOwner(plan);if(plan.lineage?.kind==='fresh')await plan.lineage.initialize(owner.ctx);if(plan.mount)session=await this.ctx.profileLoader.createMountSession(plan.generation,owner.ctx);const {prepared,runContext}=await this.preparation(plan,owner,session);this.assertPrepared(plan,prepared);if(prepared.state==='clean')await this.mountGeneration(plan,owner,session);return this.publish(plan,owner,runContext,prepared,session)}
  catch(error){try{await session?.close()}finally{await owner?.dispose()}throw error}finally{this.preparing.delete(plan.runId)}
 }
 acquire(runId:string,mode:'read'|'write'='read'):RunLease{
  if(mode!=='read'&&mode!=='write')throw Error('RUN_LEASE_MODE');const entry=this.entries.get(runId);if(!entry||entry.closing)throw Error('RUN_UNAVAILABLE');if(mode==='write'&&entry.handle.state!=='clean')throw Error('RUN_HALTED');
  let active=true;const close=this.ctx.resources.use('run.lease:'+runId,()=>{active=false;entry.leases--;if(entry.closing&&!entry.leases)entry.empty.resolve()});entry.leases++;const release=async()=>{await close()};return Object.freeze({value:entry.handle,get ctx(){if(!active)throw Error('RUN_LEASE_RELEASED');return entry.runContext},get generationPlan(){if(!active)throw Error('RUN_LEASE_RELEASED');return entry.generationPlan},close:release,release});
 }
 private closeEntry(entry:Entry):Promise<void>{
  if(entry.close)return entry.close;entry.closing=true;if(!entry.leases)entry.empty.resolve();
  return entry.close=(async()=>{await entry.empty.promise;try{await entry.session?.close()}finally{await entry.owner.dispose()}this.entries.delete(entry.handle.runId)})();
 }
 list():readonly RunSummary[]{return [...this.entries.values()].map(e=>({runId:e.handle.runId,state:e.handle.state,rootId:e.handle.root.id,leases:e.leases,closing:e.closing}))}
}
export default RunDirectory;
