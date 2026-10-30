import {chunks} from '../results/payload.js';
import type {EmbeddingProvider,EmbeddingLimits,EmbeddingIdentity,EmbeddingRequest,EmbeddingUse,EmbeddingOrigin} from '../contracts/cognition.js';
import type {Invocation} from '../contracts/common.js';
import type {AttemptLedger,AttemptIdentity} from '../attempts/ledger.js';
import type {ArtifactRef} from '../artifacts-local/store.js';
type Ledger=Pick<AttemptLedger,'claim'|'settle'|'stageEvidence'|'authority'|'evidenceChunkBytes'>&{markUnknown(identity:AttemptIdentity,reason:string,artifacts?:readonly ArtifactRef[],result?:unknown):Promise<void>};
import {EmbeddingWaiter,type SharedVector as Vector} from './waiter.js';
interface Item {text:string;bytes:number;operationId:string;inputIndex:number;waiters:Set<(value:Vector|Error)=>void>}
interface Cached extends Vector {bytes:number}
export class EmbeddingQueue {
 readonly identity:EmbeddingIdentity;
 private readonly cache=new Map<string,Cached>();private cacheBytes=0;
 private readonly items=new Map<string,Item>();private readonly queued:Item[]=[];private pendingBytes=0;
 private failure?:Error;private readonly abort=new AbortController();
 private timer?:ReturnType<typeof setTimeout>;private running?:Promise<void>;private closed=false;private closing?:Promise<void>;
 private readonly limits:EmbeddingLimits;
 constructor(readonly runId:string,private provider:EmbeddingProvider,limits:EmbeddingLimits,private ledger:Ledger,private runPhysical:<T>(body:(signal:AbortSignal)=>Promise<T>,bytes:number)=>Promise<T>=body=>body(this.abort.signal)){
  if(!Number.isSafeInteger(provider.identity.dimensions)||provider.identity.dimensions<1)throw Error('EMBEDDING_DIMENSIONS');this.identity=Object.freeze({...provider.identity});this.limits=Object.freeze({...limits});
  this.validateLimits(limits);
 }
 private limitNumber(key:string,value:unknown){if(key==='scheduling')return;if(!Number.isSafeInteger(value)||(value as number)<0)throw Error('EMBEDDING_LIMIT '+key)}
 private validateLimits(limits:EmbeddingLimits){for(const [key,value]of Object.entries(limits))this.limitNumber(key,value);const required=['maxBatchItems','maxBatchBytes','maxPendingBytes','maxTextBytes'] as const;if(required.some(key=>!limits[key])||limits.scheduling!=='fifo')throw Error('EMBEDDING_LIMIT')}

 private same(identity:EmbeddingIdentity){return ['provider','configId','model','dimensions'].every(key=>identity[key as keyof EmbeddingIdentity]===this.identity[key as keyof EmbeddingIdentity])}
 private check(request:EmbeddingRequest,context:Invocation){if(this.closed)throw Error('EMBEDDING_CLOSED');if(context.runId!==this.runId)throw Error('EMBEDDING_RUN_MISMATCH');if(!this.same(request.identity))throw Error('EMBEDDING_IDENTITY');context.signal.throwIfAborted();if(this.failure)throw this.failure}
 async embed(request:EmbeddingRequest,context:Invocation):Promise<readonly EmbeddingUse[]>{this.check(request,context);const bytes=request.texts.length*256;this.admit(request.texts,bytes);const texts=[...request.texts];this.pendingBytes+=bytes;const waiter=new EmbeddingWaiter(texts.length,context.signal,()=>{this.pendingBytes-=bytes});texts.forEach((text,index)=>this.subscribe(text,index,context.operationId,waiter));this.schedule();return waiter.promise}
 private subscribe(text:string,index:number,operationId:string,waiter:EmbeddingWaiter){const cached=this.cache.get(text);if(cached){waiter.receive(index,'cache',cached);return}const existing=this.items.get(text),item=existing??this.enqueue(text,index,operationId),callback=(value:Vector|Error)=>waiter.receive(index,existing?'singleflight':'none',value);item.waiters.add(callback);waiter.attach(()=>this.unsubscribe(item,callback))}
 private unsubscribe(item:Item,callback:(value:Vector|Error)=>void){item.waiters.delete(callback);const index=this.queued.indexOf(item);if(item.waiters.size||index<0)return;this.queued.splice(index,1);this.items.delete(item.text);this.pendingBytes-=item.bytes;if(!this.queued.length){clearTimeout(this.timer);this.timer=undefined}}
 private admit(texts:readonly string[],waiterBytes:number){let extra=waiterBytes;const added=new Set<string>();for(const text of texts){const size=Buffer.byteLength(text);if(size>this.limits.maxTextBytes||size>this.limits.maxBatchBytes)throw Error('EMBEDDING_TEXT_BYTES');if(!this.items.has(text)&&!this.cache.has(text)&&!added.has(text)){extra+=size+128;added.add(text)}}if(this.pendingBytes+extra>this.limits.maxPendingBytes)throw Error('EMBEDDING_PENDING_BYTES')}
 private enqueue(text:string,inputIndex:number,operationId:string):Item{const item:Item={text,bytes:Buffer.byteLength(text)+128,operationId,inputIndex,waiters:new Set()};this.items.set(text,item);this.queued.push(item);this.pendingBytes+=item.bytes;return item}
 private deliver(item:Item,value:Vector|Error){for(const receive of item.waiters)receive(value);item.waiters.clear()}
 private schedule(){if(this.running||this.timer||!this.queued.length||this.closed)return;this.timer=setTimeout(()=>{this.timer=undefined;this.running=this.dispatch()},this.limits.maxWaitMs)}
 private dispatch(){return Promise.resolve().then(()=>this.runPhysical(signal=>this.flush(signal),this.pendingBytes)).catch(cause=>{const error=cause instanceof Error?cause:Error(String(cause));this.failure??=error;this.rejectQueued(error)}).finally(()=>{this.running=undefined;this.schedule()})}
 private rejectQueued(error:Error){for(const item of this.queued.splice(0)){this.items.delete(item.text);this.pendingBytes-=item.bytes;this.deliver(item,error)}}
 private batch(){const batch:Item[]=[];let bytes=0;while(this.queued.length&&batch.length<this.limits.maxBatchItems){const item=this.queued[0]!;const size=Buffer.byteLength(item.text);if(batch.length&&bytes+size>this.limits.maxBatchBytes)break;this.queued.shift();batch.push(item);bytes+=size}return batch}
 private async flush(signal:AbortSignal){const batch=this.batch();if(!batch.length)return;const first=batch[0]!;const identity:AttemptIdentity={lineage:this.ledger.authority().lineage,operationId:'run/'+encodeURIComponent(this.runId)+'/embedding/'+encodeURIComponent(this.identity.provider)+'/'+encodeURIComponent(this.identity.configId)+'/'+encodeURIComponent(first.operationId)+'/'+first.inputIndex,attempt:0};let input:ArtifactRef|undefined;
  try{input=await this.ledger.stageEvidence(chunks(Buffer.from(JSON.stringify(batch.map(x=>x.text))),this.ledger.evidenceChunkBytes),'application/json');const claim=await this.ledger.claim(identity,{identity:this.identity,input},[input]);if(claim.status!=='send-permitted')throw Error('EMBEDDING_ATTEMPT_'+claim.status.toUpperCase());
   const output=await this.provider.embed(batch.map(x=>x.text),identity,AbortSignal.any([signal,this.abort.signal]));const vectors=this.validate(output.items,batch.length);const original=await this.original(output.original);await this.ledger.settle(identity,{identity:this.identity,items:vectors,usage:output.usage,original},[input,original]);
   this.succeed(batch,vectors,identity);
  }catch(cause){await this.failBatch(batch,identity,input,cause)}finally{for(const item of batch){this.items.delete(item.text);this.pendingBytes-=item.bytes}}
 }
 private succeed(batch:Item[],vectors:readonly {vector:readonly number[]}[],identity:AttemptIdentity){for(let index=0;index<batch.length;index++){const value:Vector={vector:Object.freeze([...vectors[index]!.vector]),origin:Object.freeze({attempt:Object.freeze({...identity}),itemIndex:index})};this.remember(batch[index]!.text,value);this.deliver(batch[index]!,value)}}
 private async failBatch(batch:Item[],identity:AttemptIdentity,input:ArtifactRef|undefined,cause:unknown){const error=cause instanceof Error?cause:Error(String(cause));this.failure??=error;try{await this.ledger.markUnknown(identity,error.message,[...(input?[input]:[]),...((cause as {artifacts?:ArtifactRef[]})?.artifacts??[])])}catch{}for(const item of batch)this.deliver(item,error);this.rejectQueued(error)}
 private validate(items:readonly {itemIndex:number;vector:readonly number[]}[],count:number){if(items.length!==count)throw Error('EMBEDDING_RESULT_COUNT');const ordered=[...items].sort((a,b)=>a.itemIndex-b.itemIndex);for(let i=0;i<count;i++){const item=ordered[i]!;if(item.itemIndex!==i||item.vector.length!==this.identity.dimensions||item.vector.some(value=>!Number.isFinite(value)))throw Error('EMBEDDING_RESULT_SHAPE')}return ordered}
 private async original(value:unknown):Promise<ArtifactRef>{if(value&&typeof value==='object'&&'id' in value&&'bytes' in value&&'mediaType' in value)return value as ArtifactRef;return this.ledger.stageEvidence(chunks(Buffer.from(JSON.stringify(value)),this.ledger.evidenceChunkBytes),'application/json')}
 private remember(text:string,value:Vector){const bytes=Buffer.byteLength(text)+value.vector.length*8+128;if(bytes>this.limits.maxCacheBytes||!this.limits.maxCacheItems)return;while(this.cache.size&&(this.cache.size>=this.limits.maxCacheItems||this.cacheBytes+bytes>this.limits.maxCacheBytes)){const key=this.cache.keys().next().value!;this.cacheBytes-=this.cache.get(key)!.bytes;this.cache.delete(key)}this.cache.set(text,{...value,bytes});this.cacheBytes+=bytes}
 close():Promise<void>{return this.closing??=(async()=>{this.closed=true;clearTimeout(this.timer);this.abort.abort(Error('EMBEDDING_PROVIDER_CLOSED'));for(const item of this.queued.splice(0)){this.items.delete(item.text);this.pendingBytes-=item.bytes;this.deliver(item,Error('EMBEDDING_NOT_DISPATCHED'))}try{await this.running}finally{await this.provider.close();this.cache.clear();this.cacheBytes=0}})()}
 diagnostics(){return {pendingItems:this.items.size,pendingBytes:this.pendingBytes,cacheItems:this.cache.size,cacheBytes:this.cacheBytes,physical:!!this.running,closed:this.closed}}
}
