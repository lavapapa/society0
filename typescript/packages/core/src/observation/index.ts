import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import {SqliteBranchSource} from './branch-source.js';
import {randomUUID} from 'node:crypto';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
import type {ObservationPort,ObservationStatus,ObservationQuery,ObservationPage,ObservationEvent,ObservationStream} from '@society0/host/observation-gateway';
import type {SqliteSnapshot} from '../sqlite/contracts.js';
import '../sqlite/index.js';import '../sqlite-history/index.js';import '../execution-fence/index.js';import '../run-controller/index.js';
import '@society0/host/inspector';import '@society0/host/diagnostics';import '@society0/host/resources';
export const Config=Schema.object({pageSize:Schema.natural().min(1).max(10000).default(100),includeRawProvider:Schema.const(false).default(false),maxPageBytes:Schema.natural().min(256).default(1048576),maxSnapshots:Schema.natural().min(1).default(16),snapshotAgeMs:Schema.natural().min(1).max(30000).default(30000),bufferSize:Schema.natural().min(1).default(16),maxSubscribers:Schema.natural().min(1).default(32),pollIntervalMs:Schema.natural().min(10).default(100)});
export const metadata:PluginMetadata={title:'observation',summary:'Read-only snapshot pages and bounded lossy display observations',configVersion:1,scope:'run',allowedScopes:['run'],hostAbi:'society0/host/1',configPolicy:[{path:'/pageSize',mode:'live',affectsResults:false},{path:'/includeRawProvider',mode:'live',affectsResults:false},...['maxPageBytes','maxSnapshots','snapshotAgeMs','bufferSize','maxSubscribers','pollIntervalMs'].map(path=>({path:'/'+path,mode:'recreate' as const,affectsResults:false}))]};
export const api:ApiEntry={exportPath:'./observation',provides:['observation','branchSource'].map(key=>({key,protocol:key,major:1,capabilities:[]})),requires:['database','history','inspector','diagnostics','attempts','artifacts'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module'}))};
interface PageState {snapshot:SqliteSnapshot;sql:string;parameters:readonly (null|string|number)[];offset:number;busy:boolean;timer:ReturnType<typeof setTimeout>}
interface Subscriber {queue:ObservationEvent[];dropped:number;wake?:()=>void;closed:boolean;close:()=>Promise<void>}
declare module '@society0/host' {interface Context {observation:ObservationPort}}
export class Observation extends Service implements ObservationPort {
 static Config=Config;static inject=['database','history','inspector','diagnostics','attempts','artifacts'];
 private config:ReturnType<typeof Config>;private pages=new Map<string,PageState>();private subscribers=new Set<Subscriber>();private sequence=0n;private poll?:ReturnType<typeof setInterval>;private refreshing?:Promise<void>;private closed=false;private opening=0;private active=new Set<Promise<unknown>>();
 constructor(ctx:Context,input={}){super(ctx,'observation');this.config=Config(input);ctx.effect(()=>()=>this.close())}
 async [Service.init](){await this.ctx.plugin(SqliteBranchSource)}
 async status():Promise<ObservationStatus>{
  const history=await this.ctx.history.status();const database=this.ctx.database.diagnostics();const execution=this.ctx.get('executionFence')?.snapshot();const controller=this.ctx.get('runs')?.diagnostics();
  const unknown=database.halted||database.exited||execution?.state==='halted';
  const plugins=this.ctx.inspector.plugins();const hostPlugins={total:plugins.length,entries:plugins.slice(0,this.config.pageSize).map(({entryId,status,scope,source,owner,requires,missingServices,services})=>({entryId,status,scope,source,owner,requires,missingServices,services}))};
  return {...history,runId:this.ctx.database.runId,state:unknown?'unknown':history.state,database,execution,controller,hostResources:this.ctx.get('resources')?.closeReport(),hostDiagnostics:this.ctx.diagnostics.snapshot(),hostPlugins};
 }
 query(request:ObservationQuery,signal?:AbortSignal):Promise<ObservationPage>{
  if(this.closed)return Promise.reject(Error('OBSERVATION_CLOSED'));
  const task=this.page(request,signal);this.active.add(task);void task.then(()=>this.active.delete(task),()=>this.active.delete(task));return task;
 }
 private pageSize(request:ObservationQuery){
  const size=request.pageSize??this.config.pageSize;
  if(!Number.isSafeInteger(size)||size<1||size>this.config.pageSize)throw Error('OBSERVATION_PAGE_SIZE');
  return size;
 }
 private cursorState(request:ObservationQuery,token:string){
  const state=this.pages.get(token);
  if(!state)throw Error('OBSERVATION_CURSOR_EXPIRED');
  if(request.sql!==undefined||request.parameters!==undefined)throw Error('OBSERVATION_CURSOR_QUERY');
  return state;
 }
 private async openPage(request:ObservationQuery){
  if(typeof request.sql!=='string'||!request.sql.trim())throw Error('OBSERVATION_SQL');
  if(this.pages.size+this.opening>=this.config.maxSnapshots)throw Error('OBSERVATION_SNAPSHOT_LIMIT');
  this.opening++;let snapshot:SqliteSnapshot;
  try{snapshot=await this.ctx.database.snapshot({maxAgeMs:this.config.snapshotAgeMs})}finally{this.opening--}
  const token=randomUUID();
  const state:PageState={snapshot,sql:request.sql,parameters:request.parameters??[],offset:0,busy:false,timer:setTimeout(()=>{void this.closeCursor(token).catch(()=>{})},this.config.snapshotAgeMs)};
  this.pages.set(token,state);return {token,state};
 }
 private async readPage(state:PageState,token:string,size:number,signal?:AbortSignal):Promise<ObservationPage>{
  const rows=await state.snapshot.query({sql:`SELECT * FROM (${state.sql}) LIMIT ? OFFSET ?`,parameters:[...state.parameters,BigInt(size+1),BigInt(state.offset)]},{maxRows:size+1,maxBytes:this.config.maxPageBytes});
  signal?.throwIfAborted();const more=rows.length>size;
  const selected=rows.slice(0,size).map(row=>Object.fromEntries(Object.entries(row).map(([key,value])=>[key,displayValue(value)])));
  state.offset+=selected.length;
  const page={snapshot:state.snapshot.id,revision:state.snapshot.revision,rows:selected,...more?{cursor:token}:{}};
  if(!more)await this.closeCursor(token);return page;
 }
 private async selectPage(request:ObservationQuery){
  return request.cursor?{token:request.cursor,state:this.cursorState(request,request.cursor)}:await this.openPage(request);
 }
 private async page(request:ObservationQuery,signal?:AbortSignal):Promise<ObservationPage>{
  signal?.throwIfAborted();const size=this.pageSize(request);
  const {token,state}=await this.selectPage(request);
  if(state.busy)throw Error('OBSERVATION_CURSOR_BUSY');state.busy=true;
  let aborting:Promise<void>|undefined;
  const abort=()=>{aborting=this.closeCursor(token);void aborting.catch(()=>{})};
  signal?.addEventListener('abort',abort,{once:true});
  try{
   if(signal?.aborted)abort();signal?.throwIfAborted();
   return await this.readPage(state,token,size,signal);
  }catch(error){await this.closeCursor(token);throw error}
  finally{signal?.removeEventListener('abort',abort);await aborting;state.busy=false}
 }
 async closeCursor(cursor:string){const state=this.pages.get(cursor);if(!state)return;this.pages.delete(cursor);clearTimeout(state.timer);await state.snapshot.close()}
 refresh():Promise<void>{
  if(this.closed)return Promise.resolve();if(this.refreshing)return this.refreshing;
  return this.refreshing=(async()=>{const status=await this.status();const sequence=String(++this.sequence);for(const sub of this.subscribers){if(sub.queue.length===this.config.bufferSize){sub.queue.shift();sub.dropped++}sub.queue.push({sequence,dropped:0,status});sub.wake?.();sub.wake=undefined}})().finally(()=>{this.refreshing=undefined});
 }
 private async closeSubscriber(sub:Subscriber){
  if(sub.closed)return;sub.closed=true;sub.queue=[];this.subscribers.delete(sub);sub.wake?.();
  if(!this.subscribers.size&&this.poll){clearInterval(this.poll);this.poll=undefined}
 }
 private async nextSubscriber(sub:Subscriber):Promise<IteratorResult<ObservationEvent>>{
  while(!sub.closed&&!sub.queue.length)await new Promise<void>(resolve=>{sub.wake=resolve});
  if(sub.closed)return {done:true,value:undefined};
  const value=sub.queue.shift()!;const delivered={...value,dropped:sub.dropped};sub.dropped=0;return {done:false,value:delivered};
 }
 private sample(){
  void this.refresh().catch(error=>{
   this.ctx.diagnostics.report({code:'OBSERVATION_SAMPLE_FAILED',message:String(error),phase:'task'});
   for(const item of this.subscribers)void item.close();
  });
 }
 subscribe(options:{signal?:AbortSignal}={}):ObservationStream{
  if(this.closed)throw Error('OBSERVATION_CLOSED');options.signal?.throwIfAborted();
  if(this.subscribers.size>=this.config.maxSubscribers)throw Error('OBSERVATION_SUBSCRIBER_LIMIT');
  const sub:Subscriber={queue:[],dropped:0,closed:false,close:async()=>{await this.closeSubscriber(sub);options.signal?.removeEventListener('abort',abort)}};
  const abort=()=>{void sub.close()};options.signal?.addEventListener('abort',abort,{once:true});this.subscribers.add(sub);
  if(!this.poll)this.poll=setInterval(()=>this.sample(),this.config.pollIntervalMs);this.sample();
  return {[Symbol.asyncIterator](){return this},close:sub.close,return:async()=>{await sub.close();return {done:true,value:undefined}},next:()=>this.nextSubscriber(sub)};
 }
 async close(){this.closed=true;if(this.poll)clearInterval(this.poll);for(const sub of [...this.subscribers])await sub.close();await Promise.all([...this.pages.keys()].map(key=>this.closeCursor(key)));await Promise.allSettled([...this.active]);await this.refreshing}
}
function displayValue(value:unknown){
 if(typeof value==='bigint')return {integer:String(value)};
 if(value instanceof Uint8Array)return {base64:Buffer.from(value).toString('base64')};
 return value;
}
export default Observation;
