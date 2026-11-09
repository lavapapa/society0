import type {AssistantMessage,ToolCall} from '@earendil-works/pi-ai';
import type {Context} from '@society0/host';
import type {AgentMessage} from '@earendil-works/pi-agent-core';
import type {ActivationIntent,ActivationContext,ActivationResult} from '../contracts/runtime.js';
import type {Json,Lease} from '../contracts/common.js';
import type {ThreadView,Message,PreparedMessage} from '../contracts/domain.js';
import type {StoredValue} from '../contracts/data.js';
import type {PerceptionInput} from '../contracts/perception.js';
import type {PerceptionBatch} from '../contracts/perception.js';
import type {SequencedInferenceEvent,InferenceStopReason,InferenceRequest,InferenceOutcome,InferenceExtensions,InferenceEventSink,UndispatchedToolReceipt,AgentRuntime} from '../contracts/inference.js';
import type {SqliteSnapshot,SqliteTransaction} from '../sqlite/contracts.js';
import {OrderedInferenceSink} from '../agent-runtime-pi/sink.js';
import {acquireExtensions} from '../agent-runtime-pi/index.js';
import {prepareValue} from '../results/payload.js';
import {diffDelivery,foldDelivery} from '../contracts/delivery.js';
import {actionsDeliveryChannel, canonicalJson, emptyOfferFold, type OfferFoldState, type OfferLimits} from '../contracts/affordance.js';
import {STATE_SOURCE, contextBudget, stateDeliveryChannel, type StateSource} from '../contracts/context.js';
import {deliveredKeys, offerDelivery} from './offers.js';
import {metricsOf} from '../contracts/metrics.js';
import {performance} from 'node:perf_hooks';
import type {LlmOptions} from './index.js';

/** driver对外原因：推理原因之上增加调用阶段失败。 */
export type LlmActivationReason=InferenceStopReason|'setup_error'|'perception_error'|'thread_error'|'internal_error';
/** 带机器码与触发阶段的driver错误；cause保留内层错误对象，已分类错误原样传递。 */
export class LlmActivationError extends Error {
 constructor(readonly reason:LlmActivationReason,readonly code:string,readonly stage:string,options?:{cause?:unknown}){super(code,options);this.name='LlmActivationError'}
}
const codeOf=(error:unknown)=>String((error as {code?:unknown})?.code??'');
const runtimeSetup=new Set(['INFERENCE_RUNTIME_CLOSED','INFERENCE_COMPLETION_REQUIRED','INFERENCE_BUDGET','INFERENCE_HISTORY_FIDELITY']);
const storageUnknown=new Set(['SQLITE_COMMIT_UNKNOWN','SQLITE_SEGMENT_FAILED']);
/** 事务结果合同：提交未知与段缺失归持久未知，内部不变量归内部错误，其余确定未提交。 */
function storage(error:unknown):LlmActivationReason{const code=codeOf(error);if(code==='INFERENCE_MODEL_TEXT_NOT_WELL_FORMED')return 'provider_error';if(storageUnknown.has(code))return 'persistence_unknown';return code==='SQLITE_TX_OUTCOME_MISSING'?'internal_error':'persistence_error'}
function classified(reason:LlmActivationReason,stage:string,error:unknown):LlmActivationError{return error instanceof LlmActivationError?error:new LlmActivationError(reason,codeOf(error)||'LLM_'+reason.toUpperCase(),stage,{cause:error})}
const contextLimit=()=>new LlmActivationError('context_limit','INFERENCE_CONTEXT_LIMIT','history');
/** 仅在signal确实中止时按同一reason对象、cause链或原生AbortError识别取消。 */
function aborted(error:LlmActivationError,signal:AbortSignal){if(!signal.aborted)return false;let item:unknown=error;for(let depth=0;item&&depth<5;depth++){if(item===signal.reason||(item as Error).name==='AbortError')return true;item=(item as {cause?:unknown}).cause}return false}

interface PreparedContext {
 readonly binding:Awaited<ReturnType<Context['actorTools']['bind']>>;
 readonly selected:Awaited<ReturnType<Context['modelProviders']['prepare']>>;
 readonly runtime:Lease<AgentRuntime>;
 readonly extensions:InferenceExtensions&{release():Promise<void>};
 readonly sink:OrderedInferenceSink;
 readonly history:readonly AgentMessage[];
}

export interface FoldState { history:number; percepts:number; trim:number }
export class LlmActivation {
 private operations=new Set<string>();private held:{release():Promise<void>}[]=[];private thread!:ThreadView;private controller=new AbortController();private context:ActivationContext;
 private pendingFrames:{frame:SequencedInferenceEvent;role:string;content:Json;attemptIds:readonly string[]}[]=[];private pendingEventBytes=0;private flushSequence=0;private deltaCount=0;private deliveryRecords:Json[]=[];
 private requestStarted?:number;
 private readonly offerLimits:OfferLimits;
 constructor(private ctx:Context,private config:LlmOptions,private intent:ActivationIntent,context:ActivationContext,private folds:Map<string,FoldState>=new Map()){this.context={...context,signal:AbortSignal.any([context.signal,this.controller.signal])};this.offerLimits={maxOffers:64,maxOfferBytes:contextBudget(this.config.budget).bytes('actions'),maxSchemaBytes:4096}}
 private scope(){return {actorId:this.context.actorId,momentId:this.context.momentId,logicalTime:this.context.logicalTime,query:null}}
 private async step<T>(reason:LlmActivationReason,stage:string,body:()=>T|Promise<T>):Promise<T>{try{return await body()}catch(error){throw classified(reason,stage,error)}}
 private async withView<T>(body:(view:SqliteSnapshot)=>Promise<T>):Promise<T>{
  const view=await this.step('persistence_error','db.snapshot',()=>this.ctx.database.snapshot({maxAgeMs:30000}));
  let result:T;
  try{result=await body(view)}catch(error){await view.close().catch(()=>{});throw error}
  await this.step('persistence_error','db.close',()=>view.close());
  return result;
 }
 private async transaction(label:string,body:(tx:SqliteTransaction)=>Promise<void>){
  const operationId=this.context.operationId+'/'+label;
  try{
   const result=await this.context.database.transaction(this.context.writeLease,{operationId,maxDurationMs:this.config.maxTransactionMs,maxStatements:this.config.maxStatements,maxInputBytes:this.config.maxInputBytes,maxChangedRows:this.config.maxStatements*2,maxCapturedBytes:this.config.maxInputBytes*4},body);
   this.operations.add(result.receipt.operationId);
  }catch(error){throw classified(storage(error),'transaction:'+label,error)}
 }
 private async prepareMessage(id:string,role:string,content:Json,original:Json=null):Promise<PreparedMessage>{return this.ctx.threads.prepare({id,interactionId:this.thread.interactionId,role,content,originalProviderFields:original},this.context.signal)}
 /** 自动恢复重跑：在失败那次已落盘的帧之后标记中断，不删除原帧；新内容随后按新的 operationId 命名空间追加。 */
 private async recoveryMarker(){
  const recovery=this.context.recovery;if(!recovery)return;
  const text=`上一次尝试因上游错误中断（operationId: ${recovery.baseOperationId}，第 ${recovery.attempt} 次自动恢复）。中断前的帧保留在线程中，本次重跑的内容完整追加在后面。`;
  const native={role:'system',content:text,timestamp:Date.now()} as unknown as Json;
  const message=await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(this.context.operationId+'/interrupt','system',native,{activationId:this.context.operationId,recovery:{attempt:recovery.attempt,baseOperationId:recovery.baseOperationId}}));
  await this.transaction('interrupt',tx=>this.step('thread_error','thread.append',()=>this.ctx.threads.append(tx,this.thread.id,message)));
 }
 private async readValue(value:StoredValue){
  if(value.kind==='inline')return value.value;
  const size=Number(value.ref.rawBytes);
  if(size>this.config.maxHistoryBytes)throw contextLimit();
  const chunks=[];
  for await(const chunk of this.ctx.threads.readPayload(value.ref,{offset:'0',length:size,maxBytes:this.config.maxHistoryBytes},this.context.signal))chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
 }
 private async decodeMessage(payload:StoredValue){const message=await this.readValue(payload) as Message,content=message.content as any;const native:AgentMessage=content&&typeof content==='object'&&['user','system','assistant','toolResult'].includes(content.role)?content:{role:'user',content:JSON.stringify(message),timestamp:0};const record={...native,originalProviderFields:(message.originalProviderFields??null) as Json} as unknown as Json;return {native,record,bytes:Buffer.byteLength(JSON.stringify(native)),origin:(message.originalProviderFields as {activationId?:string}|undefined)?.activationId}}
 private historyBudget(bytes:number,count:number){if(bytes>this.config.maxHistoryBytes||count>=this.config.maxHistoryMessages)throw contextLimit()}
 private messageBytes(message:AgentMessage){return Buffer.byteLength(JSON.stringify(message))}
 private isPerceptMessage(message:AgentMessage){return message.role==='user'&&typeof message.content==='string'&&message.content.trimStart().startsWith('{"source"')}
 private historyFoldText(count:number){return `历史已折叠：前 ${count} 轮对话（仅计数，原始消息仍在线程中）。`}
 private perceptHint(count:number,detail=false){return JSON.stringify({source:'percepts',truncated:count,continuation:detail?'感知超出预算，已截断较早条目；续读请查看运行记录中的完整材料。':'较早感知超出预算，已折叠；续读请查看运行记录中的完整材料。'})}
 private trimPercept(message:AgentMessage,remove:number):AgentMessage{
  let parsed:Record<string,unknown>;
  try{parsed=JSON.parse(message.content as string) as Record<string,unknown>}catch{return message}
  if(!parsed||!Array.isArray(parsed.percepts))return message;
  const percepts=[...(parsed.percepts as unknown[])],paths=Array.isArray(parsed.paths)?[...(parsed.paths as unknown[])]:[];
  const removed=Math.min(Math.max(0,remove),percepts.length);
  percepts.splice(0,removed);
  const keptPaths=paths.length>percepts.length?paths.slice(paths.length-percepts.length):paths;
  const content={...parsed,percepts,paths:keptPaths,...(removed?{truncated:(Number(parsed.truncated)||0)+removed,continuation:'感知超出预算，已截断较早条目；续读请查看运行记录中的完整材料。'}:{})};
  return {...message,content:JSON.stringify(content)} as AgentMessage;
 }
 private fitPercept(message:AgentMessage,maxBytes:number):{message:AgentMessage;removed:number}{
  let total=0;
  try{const parsed=JSON.parse(message.content as string) as Record<string,unknown>;total=Array.isArray(parsed.percepts)?parsed.percepts.length:0}catch{return {message,removed:0}}
  for(let remove=0;remove<=total;remove++){const trimmed=this.trimPercept(message,remove);if(remove===total||this.messageBytes(trimmed)<=maxBytes)return {message:trimmed,removed:remove}}
  return {message,removed:0};
 }
 private perceptHintText(count:number){return {role:'user',content:this.perceptHint(count),timestamp:0} as unknown as AgentMessage}
 private buildPercepts(messages:AgentMessage[],percepts:readonly AgentMessage[],drop:number,trim:number):AgentMessage[]{
  if(!drop&&!trim)return messages;
  const hint=drop?this.perceptHintText(drop):undefined,boundary=trim&&percepts[drop]?this.trimPercept(percepts[drop]!,trim):undefined;
  const result:AgentMessage[]=[];let inserted=false,replaced=false;
  for(const message of messages){
   if(this.isPerceptMessage(message)){
    const ordinal=percepts.indexOf(message);
    if(ordinal<drop){if(!inserted&&hint){result.push(hint);inserted=true}continue}
    if(ordinal===drop&&boundary&&!replaced){result.push(boundary);replaced=true;continue}
   }
   result.push(message);
  }
  return result;
 }
 /** percepts 槽按分页结果截断：先折整条最旧感知消息，边界消息再按条目截断；均保留续读提示。 */
 private foldPercepts(messages:AgentMessage[]):AgentMessage[]{
  const budget=contextBudget(this.config.budget),limit=budget.bytes('percepts'),target=Math.max(1,Math.floor(limit*0.6));
  const percepts=messages.filter(message=>this.isPerceptMessage(message));
  if(!percepts.length)return messages;
  const state=this.folds.get(this.thread.id);
  let drop=state?.percepts??0,trim=state?.trim??0;
  if(drop<0||drop>=percepts.length){drop=0;trim=0}
  const size=(list:readonly AgentMessage[])=>list.reduce((sum,message)=>sum+this.messageBytes(message),0);
  let used=size(percepts.slice(drop))+(drop?this.messageBytes(this.perceptHintText(drop)):0);
  if(trim&&percepts[drop])used+=this.messageBytes(this.trimPercept(percepts[drop]!,trim))-this.messageBytes(percepts[drop]!);
  if(drop&&used<=limit)return this.buildPercepts(messages,percepts,drop,trim);
  if(!drop&&!trim&&used<=limit)return messages;
  drop=0;trim=0;let remaining=size(percepts);
  while(drop<percepts.length-1&&remaining+this.messageBytes(this.perceptHintText(drop+1))>target){remaining-=this.messageBytes(percepts[drop]!);drop++}
  if(remaining+this.messageBytes(this.perceptHintText(drop))>target&&percepts[drop]){
   const fit=this.fitPercept(percepts[drop]!,Math.max(0,target-this.messageBytes(this.perceptHintText(drop))));
   trim=fit.removed;
  }
  this.folds.set(this.thread.id,{...(state??{history:0,percepts:0,trim:0}),percepts:drop,trim});
  return this.buildPercepts(messages,percepts,drop,trim);
 }
 private buildHistory(tokens:readonly ({kind:'message';message:AgentMessage}|{kind:'unit';messages:AgentMessage[]})[],units:readonly {kind:'unit';messages:AgentMessage[]}[],drop:number):AgentMessage[]{
  const dropped=new Set(units.slice(0,drop)),placeholder={role:'user',content:this.historyFoldText(drop),timestamp:0} as unknown as AgentMessage;
  const result:AgentMessage[]=[];let inserted=false;
  for(const token of tokens){
   if(token.kind==='unit'&&dropped.has(token)){if(!inserted){result.push(placeholder);inserted=true}continue}
   if(token.kind==='unit')result.push(...token.messages);else result.push(token.message);
  }
  return result;
 }
 /** history 槽超预算时只从最旧的整轮开始折，一次折到目标的约 60%；折叠点记在驱动内，两次折叠之间前缀不变。 */
 private foldHistory(messages:AgentMessage[],current:AgentMessage|undefined):AgentMessage[]{
  type Token={kind:'message';message:AgentMessage}|{kind:'unit';messages:AgentMessage[]};
  const budget=contextBudget(this.config.budget),limit=budget.bytes('history'),target=Math.max(1,Math.floor(limit*0.6));
  const tokens:Token[]=[];let unit:AgentMessage[]|undefined;
  const flush=()=>{if(unit?.length){tokens.push({kind:'unit',messages:unit});unit=undefined}};
  for(const message of messages){
   if(message===current||message.role==='system'||this.isPerceptMessage(message)){flush();tokens.push({kind:'message',message});continue}
   if(message.role==='user'){flush();unit=[message];continue}
   if(unit)unit.push(message);else unit=[message];
  }
  flush();
  const units=tokens.filter((token):token is {kind:'unit';messages:AgentMessage[]}=>token.kind==='unit');
  const bytes=(list:readonly AgentMessage[])=>list.reduce((sum,message)=>sum+this.messageBytes(message),0);
  const currentBytes=current?this.messageBytes(current):0;
  const state=this.folds.get(this.thread.id);
  let drop=state?.history??0;
  if(drop<0||drop>=units.length)drop=0;
  const placeholder=(count:number)=>this.messageBytes({role:'user',content:this.historyFoldText(count),timestamp:0} as unknown as AgentMessage);
  let remaining=bytes(units.slice(drop).flatMap(item=>item.messages));
  let used=remaining+currentBytes+(drop?placeholder(drop):0);
  if(drop&&used<=limit)return this.buildHistory(tokens,units,drop);
  if(!drop&&used<=limit)return messages;
  while(drop<units.length-1&&remaining+currentBytes+placeholder(drop+1)>target){remaining-=bytes(units[drop]!.messages);drop++}
  if(!drop)return messages;
  this.folds.set(this.thread.id,{...(state??{history:0,percepts:0,trim:0}),history:drop});
  return this.buildHistory(tokens,units,drop);
 }
 private enforceBudget(messages:AgentMessage[],current:AgentMessage|undefined):AgentMessage[]{return this.foldHistory(this.foldPercepts(messages),current)}
 private returnedAfter(messages:AgentMessage[],index:number){const returned=new Set<string>();for(let next=index+1;next<messages.length&&messages[next]!.role!=='assistant';next++){const message=messages[next]!;if(message.role==='toolResult')returned.add(message.toolCallId)}return returned}
 private async recoverCall(message:AssistantMessage,call:ToolCall,origins:ReadonlyMap<AgentMessage,string>):Promise<AgentMessage>{
  const activation=origins.get(message);
  if(!activation)throw new LlmActivationError('thread_error','INFERENCE_UNRESOLVED_TOOL','thread.history');
  const fact=await this.step('persistence_error','attempts.probe',()=>this.ctx.attempts.probe({lineage:this.ctx.attempts.authority().lineage,operationId:activation+'/undispatched/'+call.id,attempt:0}));
  if(fact.status!=='found'||fact.record.status!=='settled'||(fact.record.result as {status?:string})?.status!=='not_dispatched')throw new LlmActivationError('thread_error','INFERENCE_UNRESOLVED_TOOL','thread.history');
  return {role:'toolResult',toolCallId:call.id,toolName:call.name,content:[{type:'text',text:JSON.stringify({status:'not_dispatched',reason:'cancelled'})}],details:{status:'not_dispatched'},isError:true,timestamp:0};
 }
 private async reconcile(messages:AgentMessage[],origins:ReadonlyMap<AgentMessage,string>):Promise<AgentMessage[]>{const recovered:AgentMessage[]=[];for(let index=0;index<messages.length;index++){const message=messages[index]!;recovered.push(message);if(message.role!=='assistant')continue;const returned=this.returnedAfter(messages,index);for(const call of message.content){if(call.type!=='toolCall'||returned.has(call.id))continue;recovered.push(await this.recoverCall(message,call,origins))}}return recovered}
 private async history():Promise<{messages:AgentMessage[];delivery:Awaited<ReturnType<typeof offerDelivery>>}>{
  this.deliveryRecords=[];
  return this.withView(view=>this.step('thread_error','thread.history',async()=>{
   const messages:AgentMessage[]=[],origins=new Map<AgentMessage,string>();let cursor:string|undefined,bytes=0;
   // open() 的 messageCount 早于本激活内追加的感知消息，必须实际分页读取。
   do{
    const page=await this.ctx.threads.page(this.thread.id,{...this.scope(),momentId:this.thread.momentId},view,{cursor,maxItems:this.config.maxPageItems,maxResponseBytes:this.config.maxPageBytes});
    for(const row of page.items){
     if(row.role==='inference_event')continue;
     const decoded=await this.decodeMessage(row.payload);
     bytes+=decoded.bytes;this.historyBudget(bytes,messages.length);
     let native=decoded.native;
     if(native.role==='toolResult'&&decoded.bytes>contextBudget(this.config.budget).bytes('toolResultMax'))native={...native,content:[{type:'text',text:`工具结果过大，已折叠 ${decoded.bytes} 字节；完整内容见 /thread/${this.thread.id}/${row.id}`}]} as AgentMessage;
     messages.push(native);this.deliveryRecords.push(decoded.record);
     if(decoded.origin)origins.set(native,decoded.origin);
    }
    cursor=page.next;
   }while(cursor);
   const folded=foldDelivery(this.deliveryRecords,[actionsDeliveryChannel]).channels.get('actions') as OfferFoldState|undefined;
   const delivery=await this.step('setup_error','offers',()=>offerDelivery(this.ctx.actions,this.scope(),view,folded??emptyOfferFold(),this.offerLimits));
   return {messages:await this.reconcile(messages,origins),delivery};
  }));
 }
 private async percepts(contexts:readonly StoredValue[],paths:string[]){
  if(!this.config.perceptions.length)return;
  const logicalTime=this.context.logicalTime;
  const selected=await this.step('perception_error','perception.collect',()=>this.ctx.perceptions.acquire(this.config.perceptions,this.context));
  this.held.push(selected);
  for(let page=0;page<this.config.maxPerceptionPages;page++){
   const scope={actorId:this.context.actorId,momentId:this.context.momentId,threadId:this.thread.id,logicalTime,reason:this.intent.input};
   let batch:PerceptionBatch|undefined,committed=false;
   for(let attempt=0;!committed;attempt++){
    const current=await this.step('perception_error','perception.collect',()=>selected.value.collect(scope,{maxItems:this.config.maxPageItems,maxResponseBytes:this.config.maxPageBytes},this.context));
    batch=current;
    if(!current.reads.length)return;
    const inputs:PerceptionInput[]=[],staged:string[]=[];
    for(const read of current.reads){
     if(!read.page.items.length)continue;
     const tag=attempt?'/'+attempt:'';
     const texts=await this.step('perception_error','perception.material',()=>Promise.all(read.page.items.map(async item=>JSON.stringify({source:read.sourceId,id:item.id,kind:item.kind,provenance:item.provenance,material:await this.readValue(item.material)}))));
     const operationId=this.context.operationId+'/perception/'+page+'/'+read.sourceId+tag;
     const mounted=await this.step('setup_error','context.freeze',()=>this.ctx.contextMount.freeze(texts,{...this.context,operationId}));
     this.held.push(mounted);staged.push(...mounted.value);
     const linked=await this.step('persistence_error','artifact.stage',()=>Promise.all(texts.map(text=>prepareValue(text,16384,this.ctx.artifacts,this.context.signal))));
     const native={role:'user',content:JSON.stringify({source:read.sourceId,percepts:read.page.items,paths:mounted.value}),timestamp:Date.now()};
     inputs.push({sourceId:read.sourceId,perceptIds:read.page.items.map(item=>item.id),message:await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(operationId,'user',native)),contexts:[...contexts,...linked]});
    }
    try{
     await this.transaction('perception/'+page,tx=>this.step('perception_error','perception.commit',()=>selected.value.commit(tx,current,inputs)));
     paths.push(...staged);committed=true;
    }catch(error){
     // 并发 publish 会让感知在 collect 与 commit 之间失效：同一次激活内重新 collect 再 commit，最多重试 2 次。
     if(!(error instanceof LlmActivationError)||error.code!=='PERCEPTION_STALE'||attempt>=2)throw error;
    }
   }
   if(!batch!.reads.some(read=>read.page.hasMore))return;
  }
  throw new LlmActivationError('perception_error','PERCEPTION_PAGE_BUDGET','perception.collect');
 }
 private async input(sections:readonly string[],paths:readonly string[],contexts:readonly StoredValue[],deliveryText:string,originalExtras:Json):Promise<{appended:boolean;native?:AgentMessage}>{
  const consumerId='driver:'+this.config.name;
  const previous=await this.withView(view=>this.step('thread_error','thread.consumer',()=>this.ctx.threads.consumer(this.thread.id,consumerId,view)));
  if((previous?.cursor as {operationId?:string})?.operationId===this.context.operationId)return {appended:false};
  const parts=[...sections.filter(text=>text.length),'冻结上下文：'+paths.join(', '),'本次输入：'+JSON.stringify(this.intent.input),...(deliveryText?[deliveryText]:[])];
  const native:AgentMessage={role:'user',content:parts.join('\n'),timestamp:Date.now()};
  const original:Json={activationId:this.context.operationId,...(originalExtras as Readonly<Record<string,Json>>)};
  const message=await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(this.context.operationId+'/input','user',native as unknown as Json,original));
  await this.transaction('input',tx=>this.step('thread_error','thread.appendInput',()=>this.ctx.threads.appendInput(tx,{threadId:this.thread.id,expectedConsumer:previous??null,nextConsumer:{consumerId,cursor:{operationId:this.context.operationId}},message,contexts})));
  return {appended:true,native};
 }
 private retainsDelta(event:{type:string}):boolean{
  if(this.config.persistDeltas==='none')return false;
  if(this.config.persistDeltas==='all')return true;
  this.deltaCount++;
  return this.deltaCount%this.config.deltaSampleEvery===0;
 }
 private async flushEvents(){
  if(!this.pendingFrames.length)return;
  const frames=this.pendingFrames;
  this.pendingFrames=[];this.pendingEventBytes=0;
  const first=frames[0]!.frame.sequence,last=frames.at(-1)!.frame.sequence;
  const prepared=await Promise.all(frames.map(item=>this.step('persistence_error','thread.prepare',()=>this.prepareMessage(this.context.operationId+'/event/'+item.frame.sequence,item.role,item.content,{activationId:this.context.operationId,sequence:item.frame.sequence,attemptIds:[...item.attemptIds]}))));
  await this.transaction('events/'+(++this.flushSequence)+'/'+first+'-'+last,tx=>this.step('thread_error','thread.append',async()=>{for(const message of prepared)await this.ctx.threads.append(tx,this.thread.id,message)}));
 }
 private boundary(event:{type:string}){return event.type==='turn_end'||event.type==='tool_execution_start'}
 private measured<T extends {streamFn:(...args:any[])=>any}>(selection:T):T{
  const metrics=metricsOf(this.ctx);if(!metrics)return selection;
  return {...selection,streamFn:(...args:any[])=>{const started=performance.now();this.requestStarted=started;try{const result=selection.streamFn(...args);if(result&&typeof result.then==='function')return result.then((value:unknown)=>value,(error:unknown)=>{this.requestStarted=undefined;metrics.count('llm.requests');metrics.count('llm.failedRequests');metrics.observe('llm.requestLatencyMs',performance.now()-started);throw error});return result}catch(error){this.requestStarted=undefined;metrics.count('llm.requests');metrics.count('llm.failedRequests');metrics.observe('llm.requestLatencyMs',performance.now()-started);throw error}}};
 }
 private observeFrame(event:{type:string;message?:{role?:string;stopReason?:string}}){
  if(event.type!=='message_end'||event.message?.role!=='assistant')return;
  const metrics=metricsOf(this.ctx),started=this.requestStarted;this.requestStarted=undefined;
  if(!metrics)return;
  if(started!==undefined)metrics.observe('llm.requestLatencyMs',performance.now()-started);
  if(event.message.stopReason==='error'||event.message.stopReason==='aborted')metrics.count('llm.failedRequests');
 }
 private async event(frame:SequencedInferenceEvent,attemptIds:readonly string[]){
  const event=frame.event;this.observeFrame(event as {type:string;message?:{role?:string;stopReason?:string}});let role='inference_event',content:Json=event as unknown as Json;
  if(event.type==='message_update'&&!this.retainsDelta(event))return;
  if(event.type==='message_end'){role=event.message.role;content=event.message as unknown as Json}
  else if(event.type==='message_update')content={type:event.type,nativeEventType:event.assistantMessageEvent.type,attemptIds} as Json;
  this.pendingFrames.push({frame,role,content,attemptIds});
  this.pendingEventBytes+=Buffer.byteLength(JSON.stringify(content));
  if(this.pendingFrames.length>=this.config.maxPendingEvents||this.pendingEventBytes>=this.config.maxPendingBytes||this.boundary(event))await this.flushEvents();
 }
 private async undispatched(receipt:UndispatchedToolReceipt){
  await this.step('persistence_error','attempts.undispatched',async()=>{
   const identity={lineage:this.ctx.attempts.authority().lineage,operationId:this.context.operationId+'/undispatched/'+receipt.toolCallId,attempt:0};
   const claim=await this.ctx.attempts.claim(identity,{kind:'not_dispatched',threadId:this.thread.id,interactionId:this.thread.interactionId,receipt});
   if(claim.status==='unknown')throw Object.assign(Error('ATTEMPT_LEDGER_UNKNOWN '+claim.reason),{code:'ATTEMPT_LEDGER_UNKNOWN',reason:claim.reason});
   if(claim.status==='send-permitted'){await this.ctx.attempts.settle(identity,{status:'not_dispatched',receipt});return}
   if(claim.record.status!=='settled')throw Object.assign(Error('ATTEMPT_LEDGER_INCOMPLETE '+claim.record.status),{code:'ATTEMPT_LEDGER_INCOMPLETE',reason:claim.record.reason??claim.record.status});
  });
 }
 private threadMoment(){return this.config.threadLifetime==='actor'?'thread/actor/'+this.context.actorId:this.context.momentId}
 private async open(){this.thread=await this.withView(view=>this.step('thread_error','thread.open',()=>this.ctx.threads.open(this.context.actorId,this.threadMoment(),view)))}
 private async prompts(){try{return await this.ctx.prompts.acquire(this.context)}catch(error){if(codeOf(error)==='INFERENCE_CONTEXT_LIMIT')throw new LlmActivationError('context_limit','INFERENCE_CONTEXT_LIMIT','prompts',{cause:error});throw classified('setup_error','prompts',error)}}
 private async state():Promise<{text:string;record:Json|undefined}>{
  const lease=await this.ctx.slots.acquire(STATE_SOURCE,this.context);
  this.held.push(lease);
  if(!lease.items.length)return {text:'',record:undefined};
  const budget=contextBudget(this.config.budget),limit=Math.floor(budget.bytes('stateDelta')/Math.max(1,lease.items.length));
  const aggregate:Record<string,Json>={};
  for(const item of lease.items){
   const value=await item.value.snapshot(this.context.actorId,this.context.momentId),size=Buffer.byteLength(canonicalJson(value));
   const folded=size>limit?{omittedBytes:size,path:'/self/state/'+item.value.id}:value;
   if(folded!==null&&typeof folded==='object'&&!Array.isArray(folded)){
    for(const [key,entry] of Object.entries(folded as Readonly<Record<string,Json>>))aggregate[key]=entry;
   }else aggregate[item.id]=folded;
  }
  const folded=foldDelivery(this.deliveryRecords,[stateDeliveryChannel]);
  const segment=diffDelivery(folded,[stateDeliveryChannel],new Map([['state',{valid:[aggregate as unknown as Json],deliver:[aggregate as unknown as Json]}]]))[0];
  return {text:segment?.text??'',record:segment&&segment.text?segment.delta as Json:undefined};
 }
 private async threadHasModelTurns():Promise<boolean>{
  return this.withView(view=>this.step('thread_error','thread.history',async()=>{
   let cursor:string|undefined;
   do{
    const page=await this.ctx.threads.page(this.thread.id,{...this.scope(),momentId:this.thread.momentId},view,{cursor,maxItems:this.config.maxPageItems,maxResponseBytes:this.config.maxPageBytes});
    if(page.items.some(row=>row.role==='system'||row.role==='assistant'))return true;
    cursor=page.next;
   }while(cursor);
   return false;
  }));
 }
 private async prepareContext():Promise<PreparedContext>{
  const prompts=await this.prompts();
  this.held.push(prompts);
  const texts=[...prompts.value.contexts,JSON.stringify({kind:'activation',actorId:this.context.actorId,momentId:this.context.momentId,logicalTime:this.context.logicalTime,input:this.intent.input})];
  const frozen=await this.step('setup_error','context.freeze',()=>this.ctx.contextMount.freeze(texts,this.context));
  this.held.push(frozen);
  const contexts=await this.step('persistence_error','artifact.stage',()=>Promise.all(texts.map(text=>prepareValue(text,16384,this.ctx.artifacts,this.context.signal))));
  const paths=[...frozen.value];
  // fresh 判定发生在感知写入之前：感知消息不应让首轮 system/header 与 baseline 消失。
  const fresh=!(await this.threadHasModelTurns());
  if(fresh&&prompts.value.systemSections&&prompts.value.header.length){
   const native:AgentMessage={role:'system',content:prompts.value.header.join('\n\n'),timestamp:Date.now()};
   const message=await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(this.context.operationId+'/system','system',native as unknown as Json,{activationId:this.context.operationId}));
   await this.transaction('system',tx=>this.step('thread_error','thread.append',()=>this.ctx.threads.append(tx,this.thread.id,message)));
  }
  await this.recoveryMarker();
  await this.percepts(contexts,paths);
  const sections=prompts.value.systemSections?(prompts.value.baseline as string[]):([...prompts.value.header,...prompts.value.baseline] as string[]);
  let historyResult;
  try{historyResult=await this.history()}catch(error){await this.input(sections,paths,contexts,'',{}).catch(()=>{});throw error}
  const {messages:readHistory,delivery}=historyResult;
  const history=[...readHistory];
  const state=await this.state();
  const extras:Record<string,Json>={};
  if(state.record)extras.state=state.record;
  if(delivery.record)extras.offers=delivery.record;
  const deliveryText=[state.text,delivery.text].filter(text=>text.length>0).join('\n');
  const appended=await this.input(!prompts.value.systemSections||fresh?sections:[],paths,contexts,deliveryText,extras);
  const combined=appended.native?[...history,appended.native]:history;
  const current=appended.native??[...combined].reverse().find(message=>message.role==='user'&&!this.isPerceptMessage(message));
  const runtimeHistory=this.enforceBudget(combined,current);
  const binding=await this.step('setup_error','actor-tools',()=>this.ctx.actorTools.bind({...this.context,deliveredOffers:deliveredKeys(delivery.fold)},delivery.fold,this.offerLimits));
  this.held.push(binding);
  const selected=await this.step('provider_error','model.prepare',async()=>{const started=performance.now();try{return await this.ctx.modelProviders.prepare({...this.config,sessionId:this.thread.id,metadata:{session_id:this.thread.id}},this.context)}finally{metricsOf(this.ctx)?.observe('llm.queueWaitMs',performance.now()-started)}});
  this.held.push(selected);
  const runtime=await this.step('setup_error','runtime.acquire',()=>this.ctx.agentRuntimes.acquire(this.config.runtime));
  this.held.push(runtime);
  const extensions=await this.step('setup_error','runtime.acquire',()=>acquireExtensions(this.ctx,this.context));
  this.held.push(extensions);
  const sink=new OrderedInferenceSink({maxPendingEvents:this.config.maxPendingEvents,maxPendingBytes:this.config.maxPendingBytes},frame=>this.event(frame,selected.value.attemptIds??[]),receipt=>this.undispatched(receipt),error=>this.controller.abort(error));
  return {binding,selected,runtime,extensions,sink,history:runtimeHistory};
 }
 private async runRuntime(runtime:Lease<AgentRuntime>,request:InferenceRequest,sink:InferenceEventSink,extensions:InferenceExtensions):Promise<InferenceOutcome>{
  try{return await runtime.value.run(request,this.context,sink,extensions)}
  catch(error){if(error instanceof LlmActivationError)throw error;throw classified(runtimeSetup.has(codeOf(error))?'setup_error':'internal_error','runtime.run',error)}
 }
 private async infer(prepared:PreparedContext):Promise<ActivationResult>{
  const {binding,selected,runtime,extensions,sink,history}=prepared;
  const actionReceipts={read:()=>this.step('persistence_error','actions.receipts',()=>binding.value.actionReceipts.read())};
  const outcome=await this.runRuntime(runtime,{threadId:this.thread.id,interactionId:this.thread.interactionId,history,prompts:[],tools:binding.value.tools,actionReceipts,selection:this.measured(selected.value),budget:{maxRequests:this.config.maxRequests,maxToolCalls:this.config.maxToolCalls,maxTurns:this.config.maxTurns},minOutputTokens:this.config.budget.output,requiredActions:this.config.requiredActions,toolChoice:this.config.toolChoice,textualToolCallRetries:this.config.textualToolCallRetries,failOnToolError:this.config.failOnToolError},sink,extensions);
  const incompleteStream=outcome.status==='incomplete'&&(outcome.reason==='provider_error'||outcome.reason==='cancelled'||outcome.reason==='transport_unknown');
  if(!incompleteStream)await this.flushEvents();
  for(const item of outcome.actionReceipts)for(const operationId of item.receipt.requiredOperations??[])this.operations.add(operationId);
  return {status:outcome.status,value:{reason:outcome.reason,requests:outcome.requests,toolCalls:outcome.toolCalls??0,explorationErrors:outcome.explorationErrors??0,actRecoverable:outcome.actRecoverable??0,actFailed:outcome.actFailed??0,requiredSatisfied:outcome.requiredSatisfied===true,...(outcome.warnings?.length?{warnings:[...outcome.warnings]}:{})},evidence:outcome as unknown as Json,requiredOperations:[...this.operations]};
 }
 private failureReason(failure:LlmActivationError):LlmActivationReason{
  const unknown=failure.reason==='persistence_unknown'||failure.reason==='transport_unknown';
  return unknown||failure.reason==='context_limit'||!aborted(failure,this.context.signal)?failure.reason:'cancelled';
 }
 private failureEvidence(failure:LlmActivationError):Json{
  const code=codeOf(failure.cause);
  const cause=failure.cause as {bytes?:number;maxBytes?:number}|undefined;
  const limit=typeof cause?.bytes==='number'&&typeof cause?.maxBytes==='number'?{bytes:cause.bytes,maxBytes:cause.maxBytes}:null;
  return {error:String(failure.cause??failure),code:failure.code,stage:failure.stage,committed:code==='SQLITE_SEGMENT_FAILED'?true:code==='SQLITE_COMMIT_UNKNOWN'?null:false,receipt:(failure.cause as {receipt?:Json})?.receipt??null,...(limit?{limit}:{})};
 }
 private async activate():Promise<ActivationResult>{
  try{await this.open();return await this.infer(await this.prepareContext())}
  catch(error){
   const failure=classified('internal_error','unclassified',error);
   return {status:'incomplete',value:{reason:this.failureReason(failure)},evidence:this.failureEvidence(failure),requiredOperations:[...this.operations]};
  }
 }
 async run():Promise<ActivationResult>{
  const result=await this.activate(),failures:string[]=[];
  for(const lease of this.held.reverse())try{await lease.release()}catch(error){failures.push(String(error))}
  if(!failures.length)return result;
  const reason=(result.value as {reason?:string})?.reason,unknown=reason==='transport_unknown'||reason==='persistence_unknown';
  return {...result,status:'incomplete',value:unknown?result.value:{reason:'persistence_error'},evidence:{activation:result.evidence,releaseFailures:failures}};
 }
}
