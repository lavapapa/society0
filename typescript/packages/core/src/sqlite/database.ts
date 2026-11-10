import type {ConnectionState} from '@society0/sqlite-native';
import {assertLiteral} from '@society0/host/config-resolvers';
import {AsyncLocalStorage} from 'node:async_hooks';
import {isDeepStrictEqual} from 'node:util';
import {randomUUID} from 'node:crypto';
import {availableParallelism} from 'node:os';
import pLimit from 'p-limit';
import type {ExecutionGate,WriteLease,PublicationLease,CommitPermit} from '../execution-fence/state.js';
import {SqlRpc} from './rpc.js';
import {ReaderPool} from './readers.js';
import {fault,inputBytes,rpcBytes,rpcData,positive,type DatabaseOptions,type TransactionRequest,type SqliteTransaction,type SqliteSnapshot,type CommitReceipt,type CommitProbe,type CommitPhase,type CommitFailure,type SqliteRuntimeMetadata,type SqliteMigration,type SqliteSchemaVersion} from './contracts.js';
export interface PhysicalTasks {run<T>(label:string,task:(signal:AbortSignal)=>Promise<T>,options?:{signal?:AbortSignal;bytes?:number}):Promise<T>&{settled:Promise<void>}}
const inTransaction=new AsyncLocalStorage<Database>();
type Stage<R>={dispatched:boolean;begun:boolean;done:boolean;result?:R;error?:unknown;early:any};
function stageOf<R>():Stage<R>{const early=Promise.withResolvers<never>();void early.promise.catch(()=>{});return {dispatched:false,begun:false,done:false,early}}
function finish<R>(stage:Stage<R>,outcome:{result:R}|{error:unknown}){if(stage.done)return;stage.done=true;Object.assign(stage,outcome)}
function record<R>(stage:Stage<R>,work:Promise<R>):Promise<R>{return work.then(result=>{finish(stage,{result});return result},error=>{finish(stage,{error});throw error})}
function settle<R>(stage:Stage<R>):R{if(!stage.done)throw fault('SQLITE_TX_OUTCOME_MISSING','internal invariant: physical settled without outcome');if('result' in stage)return stage.result as R;throw stage.error}
function abortIdle(stage:Stage<unknown>,signal:AbortSignal):()=>void{const abort=()=>{if(!stage.begun)stage.early.reject(signal.reason)};signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();return()=>signal.removeEventListener('abort',abort)}

function operationRelease(inflight:Set<string>,operationId:string):()=>void{
 let owned=true;
 return()=>{if(owned){owned=false;inflight.delete(operationId)}};
}
function earlyOutcome<R>(stage:Stage<R>,release:()=>void):Promise<never>{
 return stage.early.promise.catch((error:unknown)=>{if(!stage.begun)release();throw error});
}

export class Database {
 readonly runId:string;readonly metadata:SqliteRuntimeMetadata;
 readonly options:Required<DatabaseOptions>;
 private rpc:SqlRpc;private control=new Int32Array(new SharedArrayBuffer(4));
 private writer=pLimit({concurrency:1,rejectOnClear:true});private pendingBytes=0;private closing?:Promise<void>;private halted=false;private diagnostic=false;private lastCommitFailure?:CommitFailure;
 private transactions=new Set<Promise<unknown>>();private snapshots=new Set<SqliteSnapshot>();private counters={commits:0,fsyncs:0};private transactionIds=new WeakMap<SqliteTransaction,string>();
 private probes=new Set<Promise<void>>();private openingSnapshots=0;private readers?:ReaderPool;private snapshotWaiters:{resolve:()=>void;reject:(error:Error)=>void;signal?:AbortSignal}[]=[];private inflight=new Set<string>();private txCalls=new WeakMap<SqliteTransaction,(op:string,args:unknown)=>Promise<any>>();
 private constructor(options:DatabaseOptions,private fence:Pick<ExecutionGate,'enter'|'halt'|'assertPublication'>,private tasks?:PhysicalTasks){
  this.options={synchronous:'full',cacheSize:-65536,readerWorkers:0,busyTimeoutMs:5000,maxQueuedBytes:32*1024*1024,maxPending:128,maxTransactionMs:30000,maxQueryBytes:1048576,maxQueryRows:10000,maxSnapshots:16,maxSnapshotAgeMs:30000,maxInputBytes:1048576,maxInlineBytes:1048576,...options};
  for(const [key,value] of Object.entries(this.options))if(typeof value==='number'&&key!=='cacheSize'&&key!=='readerWorkers')positive(value,key);if(!Number.isSafeInteger(this.options.cacheSize)||!this.options.cacheSize)throw fault('SQLITE_BUDGET','cacheSize');if(!this.options.readerWorkers)this.options.readerWorkers=Math.min(4,Math.max(1,Math.floor(availableParallelism()/4)));positive(this.options.readerWorkers,'readerWorkers');
  this.runId=options.runId;this.rpc=new SqlRpc({options:this.options,control:this.control},this.options.maxQueuedBytes,this.options.maxPending);
  this.metadata=Object.freeze({
   read:async(snapshot:SqliteSnapshot)=>{if(snapshot.runId!==this.runId)throw fault('SQLITE_RUN_ID');const rows=await snapshot.query({sql:'SELECT value FROM s0_frontier WHERE id=1'},{maxRows:1,maxBytes:this.options.maxQueryBytes});if(!rows[0])throw fault('SQLITE_FRONTIER_MISSING');return JSON.parse(String(rows[0].value))},
   complete:async(tx:SqliteTransaction,frontier:any,requiredOperations:readonly string[])=>{const call=this.txCalls.get(tx);if(!call)throw fault('SQLITE_TX_EXPIRED');await call('metadata',{frontier,requiredOperations})},
  });
 }
 static async open(options:DatabaseOptions,fence:Pick<ExecutionGate,'enter'|'halt'|'assertPublication'>,tasks?:PhysicalTasks):Promise<Database>{const db=new Database(options,fence,tasks);try{const ready=await db.rpc.call('ready');db.diagnostic=ready.reopened;db.readers=new ReaderPool(db.options,db.options.readerWorkers);await db.readers.ready();return db}catch(error){await db.readers?.close().catch(()=>{});await db.rpc.worker.terminate();await db.rpc.exited;throw error}}
 private available(){if(this.closing)throw fault('SQLITE_CLOSED');if(this.halted)throw fault('SQLITE_HALTED');if(this.diagnostic)throw fault('SQLITE_DIAGNOSTIC')}
  private completeAdmission(lease:PublicationLease,point:Parameters<Database['activateFromCompletePoint']>[1]):void{
  if(this.closing||this.halted)throw fault('SQLITE_HALTED');if(!this.diagnostic)throw fault('SQLITE_ALREADY_ACTIVE');
  this.fence.assertPublication(lease);
  if(!point.id||lease.runId!==this.runId||point.runId!==this.runId||point.revision!==lease.sealedRevision)throw fault('SQLITE_COMPLETE_POINT_MISMATCH');
  }
  private async completeSnapshot(snapshot:SqliteSnapshot,point:Parameters<Database['activateFromCompletePoint']>[1]):Promise<void>{
   const frontier=await this.metadata.read(snapshot);const expectedSchema=new Map(point.schema.map(version=>[version.owner,version.version]));
   if(snapshot.revision!==point.revision||!isDeepStrictEqual(frontier,point.frontier)||expectedSchema.size!==point.schema.length||!isDeepStrictEqual(new Map(snapshot.schema.map(version=>[version.owner,version.version])),expectedSchema))throw fault('SQLITE_COMPLETE_POINT_MISMATCH');
  }
 /** PR05 validates the durable descriptor and dependencies before this explicit admission step. */
 async activateFromCompletePoint(lease:PublicationLease,point:{id:string;runId:string;revision:string;frontier:import('./contracts.js').RuntimeFrontier;schema:readonly SqliteSchemaVersion[]}):Promise<void>{
   this.completeAdmission(lease,point);
   const snapshot=await this.snapshot({maxAgeMs:this.options.maxSnapshotAgeMs});
   try{await this.completeSnapshot(snapshot,point);this.fence.assertPublication(lease);this.diagnostic=false}
   finally{await snapshot.close()}
  }
 private readCall<T=any>(op:string,args:unknown={},cancelNative?:()=>void):Promise<T>{const bytes=rpcBytes(args);if(bytes>this.options.maxQueuedBytes)return Promise.reject(fault('SQLITE_QUEUE_LIMIT'));const payload=rpcData(args);return this.tasks?this.tasks.run('sqlite.'+op,signal=>{if(cancelNative)signal.addEventListener('abort',cancelNative,{once:true});return this.rpc.call<T>(op,payload,signal).finally(()=>{if(cancelNative)signal.removeEventListener('abort',cancelNative)})},{bytes}):this.rpc.call<T>(op,payload)}
 readRevision():Promise<string>{return this.readCall('revision')}
 async applyMigrations(migrations:readonly SqliteMigration[]):Promise<readonly SqliteSchemaVersion[]>{this.available();if(this.transactions.size)throw fault('SQLITE_BUSY');try{return await this.readCall('migrate',{migrations})}catch(error){this.halted=true;this.fence.halt('SCHEMA_FAILED');throw error}}
  private transactionRequest(request:TransactionRequest):void{
   for(const key of ['maxDurationMs','maxStatements','maxInputBytes','maxChangedRows','maxCapturedBytes'] as const)positive(request[key],key);
   if(request.maxFrameworkStatements!==undefined)positive(request.maxFrameworkStatements,'maxFrameworkStatements');
   if(request.maxDurationMs>this.options.maxTransactionMs||request.maxInputBytes>this.options.maxInputBytes)throw fault('SQLITE_TX_BUDGET');
   if(typeof request.operationId!=='string'||!request.operationId||this.inflight.has(request.operationId))throw fault('OPERATION_EXISTS');
  }
  private transactionAdmission(lease:WriteLease,request:TransactionRequest):void{
   this.available();if(inTransaction.getStore()===this)throw fault('SQLITE_NESTED_TRANSACTION');
   if(lease.runId!==this.runId)throw fault('FENCE_LEASE');
    this.transactionRequest(request);
   if(this.transactions.size>=this.options.maxPending+1||this.writer.pendingCount>=this.options.maxPending||this.pendingBytes+request.maxInputBytes>this.options.maxQueuedBytes)throw fault('SQLITE_QUEUE_LIMIT');
  }
 transaction<T>(lease:WriteLease,request:TransactionRequest,body:(tx:SqliteTransaction)=>Promise<T>):Promise<{value:T;receipt:CommitReceipt}>{
  try{
   this.transactionAdmission(lease,request);
   const permit=this.fence.enter(lease);this.inflight.add(request.operationId);const releaseId=operationRelease(this.inflight,request.operationId);this.pendingBytes+=request.maxInputBytes;
   const stage=stageOf<{value:T;receipt:CommitReceipt}>();const stops=[abortIdle(stage,permit.signal)];let settled:Promise<void>;
   const physical=(signal:AbortSignal)=>{stage.dispatched=true;stops.push(abortIdle(stage,signal));return record(stage,this.writer(()=>this.perform(permit,request,body,signal,stage)))};
   try{
    if(this.tasks){const task=this.tasks.run('sqlite.transaction',physical,{signal:permit.signal,bytes:request.maxInputBytes});void task.catch(error=>{if(!stage.dispatched){finish(stage,{error});stage.early.reject(error)}});settled=task.settled}
    else settled=physical(permit.signal).then(()=>{},()=>{});
   }catch(error){for(const stop of stops)stop();permit.release();releaseId();this.pendingBytes-=request.maxInputBytes;throw error}
   this.transactions.add(settled);void settled.then(()=>{for(const stop of stops)stop();this.pendingBytes-=request.maxInputBytes;releaseId();permit.release();this.transactions.delete(settled)});
   return Promise.race([settled.then(()=>settle(stage)),earlyOutcome(stage,releaseId)]);
  }catch(error){return Promise.reject(error)}
 }
 private forgetTransaction(tx:SqliteTransaction|undefined):void{if(tx){this.transactionIds.delete(tx);this.txCalls.delete(tx)}}
 private async unknownCommit(error:unknown,id:string,operationId:string):Promise<never>{
  const cause=error as Error&{sqliteCode?:number;stopReason?:string};
  const phase=(error as {code?:string})?.code==='SQLITE_WORKER_LOST'?'transport':'commit';
  this.lastCommitFailure={operationId,tx:id,phase,message:String(error),sqliteCode:cause.sqliteCode,stopReason:cause.stopReason};
  const unknown=Object.assign(fault('SQLITE_COMMIT_UNKNOWN',String(error)),{operationId,cause:error});
  this.halted=true;this.fence.halt('SQLITE_COMMIT_UNKNOWN');
  await this.rpc.worker.terminate();await this.rpc.exited;throw unknown;
 }
 private segmentFailed(error:unknown,id:string,operationId:string):never{
  const cause=error as Error&{receipt?:CommitReceipt;sqliteCode?:number;stopReason?:string};
  this.lastCommitFailure={operationId,tx:id,phase:'segment',message:String(error),sqliteCode:cause.sqliteCode,stopReason:cause.stopReason};
  this.halted=true;this.fence.halt('SQLITE_SEGMENT_FAILED');
  throw Object.assign(fault('SQLITE_SEGMENT_FAILED',String(error)),{operationId,committed:true,receipt:cause.receipt,cause:error});
 }
 private async workerLost(error:unknown):Promise<never>{this.halted=true;this.fence.halt('SQLITE_WORKER_LOST');await this.rpc.exited;throw error}
 private async rollbackFailure(error:unknown,id:string):Promise<never>{try{await this.rpc.call('rollback',{tx:id})}catch{this.halted=true;this.fence.halt('SQLITE_WORKER_LOST');await this.rpc.worker.terminate();await this.rpc.exited}throw error}
 private async commitFailure(error:unknown,failure:{commitPhase?:string;notSent?:boolean},id:string,operationId:string):Promise<never>{
  if(failure.commitPhase==='segment')return this.segmentFailed(error,id,operationId);
  if(failure.commitPhase==='prepare'||failure.notSent)return this.rollbackFailure(error,id);
  return this.unknownCommit(error,id,operationId);
 }
 private async preCommitFailure(error:unknown,failure:{code?:string},begun:boolean,id:string):Promise<never>{
  if(this.rpc.snapshot().exited||failure.code==='SQLITE_WORKER_LOST')return this.workerLost(error);
  if(begun)return this.rollbackFailure(error,id);
  throw error;
 }
 private async failedTransaction(error:unknown,commitStarted:boolean,begun:boolean,id:string,operationId:string):Promise<never>{
  const failure=error as {commitPhase?:string;notSent?:boolean;code?:string};
  if(commitStarted)return this.commitFailure(error,failure,id,operationId);
  return this.preCommitFailure(error,failure,begun,id);
 }
 private async perform<T>(permit:CommitPermit,request:TransactionRequest,body:(tx:SqliteTransaction)=>Promise<T>,signal:AbortSignal,stage:Stage<unknown>):Promise<{value:T;receipt:CommitReceipt}>{
  signal.throwIfAborted();permit.assertActive();this.available();Atomics.store(this.control,0,0);
  const cancel=()=>Atomics.store(this.control,0,1);signal.addEventListener('abort',cancel,{once:true});
  const id=randomUUID();let active=true,commitStarted=false,begun=false;
  const work=new Set<Promise<unknown>>();let failure:unknown;
  const check=()=>{if(!active)throw fault('SQLITE_TX_EXPIRED');signal.throwIfAborted();permit.assertActive()};
  const call=(op:string,args:unknown)=>{try{check();if(op==='metadata')assertLiteral((args as {frontier:unknown}).frontier);const p=this.rpc.call(op,{tx:id,...args as object},signal);work.add(p);void p.then(()=>work.delete(p),error=>{failure??=error;work.delete(p)});return p}catch(error){failure??=error;return Promise.reject(error)}};
  const expired=Promise.withResolvers<never>();
  const timer=setTimeout(()=>{active=false;cancel();expired.reject(fault('SQLITE_TX_TIMEOUT'))},request.maxDurationMs);
  const abort=()=>{active=false;expired.reject(signal.reason)};signal.addEventListener('abort',abort,{once:true});
  void expired.promise.catch(()=>{});
  let tx:SqliteTransaction|undefined;let callback:Promise<T>|undefined;
  try{
   stage.begun=true;const start=await this.rpc.call('begin',{tx:id,request},signal);begun=true;
   tx=Object.freeze({id,operationId:request.operationId,runId:this.runId,baseRevision:start.baseRevision,
    execute:(statement:any,options?:any)=>call('execute',{statement,...(options?.framework?{framework:true}:{})}),query:(statement:any,bounds:any,options?:any)=>call('query',{statement,bounds,...(options?.framework?{framework:true}:{})}),executeMany:(sql:string,parameters:any,bounds:any,options?:any)=>call('many',{sql,parameters,bounds,...(options?.framework?{framework:true}:{})})});
   this.transactionIds.set(tx,id);this.txCalls.set(tx,call);
   callback=inTransaction.run(this,()=>Promise.resolve().then(()=>body(tx!)));void callback.catch(()=>{});
   const value=await Promise.race([callback,expired.promise]);
   await Promise.allSettled([...work]);if(failure)throw failure;check();active=false;this.transactionIds.delete(tx);this.txCalls.delete(tx);
   if(rpcBytes(value)>this.options.maxQueryBytes)throw fault('SQLITE_RETURN_BUDGET');
   commitStarted=true;signal.removeEventListener('abort',cancel);signal.removeEventListener('abort',abort);clearTimeout(timer);
   const receipt=await this.rpc.call<CommitReceipt>('commit',{tx:id});if(receipt.counters)this.counters={...receipt.counters};return {value,receipt};
  }catch(error){
   active=false;this.forgetTransaction(tx);
   return await this.failedTransaction(error,commitStarted,begun,id,request.operationId);
  }finally{clearTimeout(timer);signal.removeEventListener('abort',cancel);signal.removeEventListener('abort',abort);await callback?.catch(()=>{});Atomics.store(this.control,0,0)}
 }
 async snapshot({maxAgeMs,signal}:{maxAgeMs:number;signal?:AbortSignal}):Promise<SqliteSnapshot>{
  if(this.closing)throw fault('SQLITE_CLOSED');if(inTransaction.getStore()===this)throw fault('SQLITE_NESTED_SNAPSHOT');positive(maxAgeMs,'maxAgeMs');if(maxAgeMs>this.options.maxSnapshotAgeMs)throw fault('SQLITE_SNAPSHOT_BUDGET');
  await this.reserveSnapshot(signal);
  const reader=this.readers!.pick();const id=randomUUID(),control=new Int32Array(new SharedArrayBuffer(4));const cancelNative=()=>Atomics.store(control,0,1);
  let start:any;try{start=await reader.call('snapshot',{id,maxAgeMs,control:control.buffer},signal)}catch(error){this.openingSnapshots--;this.wakeSnapshot();throw error}
  this.openingSnapshots--;if(this.closing){await reader.call('snapshotClose',{id}).catch(()=>{});throw fault('SQLITE_CLOSED')}let closed=false;let closing:Promise<void>|undefined;
  const check=()=>{if(closed||this.closing)throw fault('SQLITE_SNAPSHOT_EXPIRED')};
  const snapshot:SqliteSnapshot=Object.freeze({id,runId:this.runId,...start,
   query:async(statement:any,bounds:any)=>{check();return reader.call('snapshotQuery',{id,statement,bounds},signal)},
   readBlobRange:async(request:any)=>{check();return reader.call('blob',{id,request},signal)},
   close:()=>closing??=(async()=>{closed=true;cancelNative();clearTimeout(timer);this.snapshots.delete(snapshot);this.wakeSnapshot();await reader.call('snapshotClose',{id})})(),
  });const timer=setTimeout(()=>{void snapshot.close().catch(()=>{})},maxAgeMs);this.snapshots.add(snapshot);return snapshot;
 }
 private async reserveSnapshot(signal?:AbortSignal):Promise<void>{while(this.snapshots.size+this.openingSnapshots>=this.options.maxSnapshots)await this.waitSnapshot(signal);this.openingSnapshots++}
 private waitSnapshot(signal?:AbortSignal):Promise<void>{return new Promise((resolve,reject)=>{const abort=()=>{const index=this.snapshotWaiters.indexOf(entry);if(index>=0)this.snapshotWaiters.splice(index,1);reject(signal!.reason)};const entry={resolve:()=>{signal?.removeEventListener('abort',abort);resolve()},reject:(error:Error)=>{signal?.removeEventListener('abort',abort);reject(error)},signal};if(signal?.aborted){reject(signal.reason);return}signal?.addEventListener('abort',abort,{once:true});this.snapshotWaiters.push(entry)})}
 private wakeSnapshot(){const entry=this.snapshotWaiters.shift();if(entry)entry.resolve()}

 probe(operationId:string):Promise<CommitProbe>{
  if(this.closing)return Promise.resolve({status:'unknown',reason:'database closed'});
  let work:Promise<CommitProbe>,settled:Promise<void>;
  if(this.tasks){const task=this.tasks.run('sqlite.probe',()=>this.performProbe(operationId));work=task;settled=task.settled}else{work=this.performProbe(operationId);settled=work.then(()=>{},()=>{})}
  this.probes.add(settled);void settled.then(()=>this.probes.delete(settled));return work;
 }
 private async performProbe(operationId:string):Promise<CommitProbe>{
  try{
   if(this.transactions.size)return {status:'unknown',reason:'accepted transaction unsettled'};
   if(!this.rpc.snapshot().exited)return await this.rpc.call('probe',{operationId});
   await this.rpc.exited;const probe=new SqlRpc({options:this.options,probeOnly:true},this.options.maxQueuedBytes,this.options.maxPending);
   try{return await probe.call('probe',{operationId})}finally{await probe.close()}
  }catch(error){return {status:'unknown',reason:String(error)}}
 }
 nativeDiagnostics():Promise<ConnectionState & {schemaEnumerations:number}>{return this.readCall('state')}
 diagnostics(){const writer=this.rpc.snapshot(),reader=this.readers?.diagnostics();return {...writer,...this.counters,synchronous:this.options.synchronous??'full',active:writer.active+(reader?.active??0),pending:writer.pending+(reader?.pending??0),queuedBytes:writer.queuedBytes+(reader?.queuedBytes??0),pendingTransactionBytes:this.pendingBytes,inflightOperations:this.inflight.size,snapshots:this.snapshots.size,snapshotQueue:this.snapshotWaiters.length,openingSnapshots:this.openingSnapshots,readers:this.options.readerWorkers,readerPool:reader,halted:this.halted,diagnostic:this.diagnostic,lastCommitFailure:this.lastCommitFailure}}
 close():Promise<void>{return this.closing??=(async()=>{for(const waiter of this.snapshotWaiters.splice(0))waiter.reject(fault('SQLITE_CLOSED'));await Promise.allSettled([...this.transactions,...this.probes]);await Promise.allSettled([...this.snapshots].map(s=>s.close()));await this.readers?.close();await this.rpc.close()})()}
}
