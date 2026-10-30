import {Worker} from 'node:worker_threads';
import pLimit from 'p-limit';
import {fault,rpcBytes,rpcData} from './contracts.js';
/** p-limit holds the physical response, never a cancel-raced waiter. */
export class SqlRpc {
 readonly worker:Worker;readonly exited:Promise<void>;
 private limit=pLimit({concurrency:1,rejectOnClear:true});private bytes=0;private sequence=0;private dead=false;
 private pending=new Map<number,{resolve:(value:any)=>void;reject:(error:Error)=>void}>();
 constructor(data:unknown,private maxBytes:number,private maxPending:number){
  this.worker=new Worker(new URL('./worker.js',import.meta.url),{workerData:data,execArgv:[]});
  this.exited=new Promise(resolve=>this.worker.once('exit',()=>{this.dead=true;for(const p of this.pending.values())p.reject(fault('SQLITE_WORKER_LOST'));this.pending.clear();resolve()}));
  this.worker.on('error',error=>{for(const p of this.pending.values())p.reject(error);this.pending.clear()});
  this.worker.on('message',message=>{const p=this.pending.get(message.id);if(!p)return;this.pending.delete(message.id);message.error?p.reject(Object.assign(new Error(message.error.message),{code:message.error.code,sqliteCode:message.error.sqliteCode,stopReason:message.error.stopReason,commitPhase:message.error.commitPhase,receipt:message.error.receipt})):p.resolve(message.value)});
 }
 call<T=any>(op:string,args:unknown={},signal?:AbortSignal):Promise<T>{
  if(this.dead)return Promise.reject(Object.assign(fault('SQLITE_WORKER_LOST'),{notSent:true}));
  const bytes=rpcBytes(args)+op.length*2+32;
  if(this.bytes+bytes>this.maxBytes||this.limit.pendingCount>=this.maxPending)return Promise.reject(Object.assign(fault('SQLITE_QUEUE_LIMIT',`requestBytes=${bytes}, heldBytes=${this.bytes}, maxBytes=${this.maxBytes}, pending=${this.limit.pendingCount}, maxPending=${this.maxPending}`),{notSent:true}));
  const payload=rpcData(args);this.bytes+=bytes;
  return this.limit(async()=>{
   signal?.throwIfAborted();if(this.dead)throw Object.assign(fault("SQLITE_WORKER_LOST"),{notSent:true});
   const id=++this.sequence;
   return new Promise<T>((resolve,reject)=>{this.pending.set(id,{resolve,reject});this.worker.postMessage({id,op,args:payload})});
  }).finally(()=>{this.bytes-=bytes});
 }
 snapshot(){return {queuedBytes:this.bytes,active:this.limit.activeCount,pending:this.limit.pendingCount,requests:this.sequence,exited:this.dead}}
 async close():Promise<void>{if(!this.dead)await this.call('close');await this.exited}
}
