import type {AssistantMessage,ToolCall} from '@earendil-works/pi-ai';
import type {Context} from '@society0/host';
import type {AgentMessage} from '@earendil-works/pi-agent-core';
import type {ActivationIntent,ActivationContext,ActivationResult} from '../contracts/runtime.js';
import type {Json,Lease} from '../contracts/common.js';
import type {ThreadView,Message,PreparedMessage} from '../contracts/domain.js';
import type {StoredValue} from '../contracts/data.js';
import type {PerceptionInput} from '../contracts/perception.js';
import type {SequencedInferenceEvent,InferenceStopReason,InferenceRequest,InferenceOutcome,InferenceExtensions,InferenceEventSink,UndispatchedToolReceipt,AgentRuntime} from '../contracts/inference.js';
import type {SqliteSnapshot,SqliteTransaction} from '../sqlite/contracts.js';
import {OrderedInferenceSink} from '../agent-runtime-pi/sink.js';
import {acquireExtensions} from '../agent-runtime-pi/index.js';
import {prepareValue} from '../results/payload.js';
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
function storage(error:unknown):LlmActivationReason{const code=codeOf(error);if(storageUnknown.has(code))return 'persistence_unknown';return code==='SQLITE_TX_OUTCOME_MISSING'?'internal_error':'persistence_error'}
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

export class LlmActivation {
 private operations=new Set<string>();private held:{release():Promise<void>}[]=[];private thread!:ThreadView;private controller=new AbortController();private context:ActivationContext;
 constructor(private ctx:Context,private config:LlmOptions,private intent:ActivationIntent,context:ActivationContext){this.context={...context,signal:AbortSignal.any([context.signal,this.controller.signal])}}
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
 private async readValue(value:StoredValue){
  if(value.kind==='inline')return value.value;
  const size=Number(value.ref.rawBytes);
  if(size>this.config.maxHistoryBytes)throw contextLimit();
  const chunks=[];
  for await(const chunk of this.ctx.threads.readPayload(value.ref,{offset:'0',length:size,maxBytes:this.config.maxHistoryBytes},this.context.signal))chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
 }
 private async decodeMessage(payload:StoredValue){const message=await this.readValue(payload) as Message,content=message.content as any;const native:AgentMessage=content&&typeof content==='object'&&['user','assistant','toolResult'].includes(content.role)?content:{role:'user',content:JSON.stringify(message),timestamp:0};return {native,bytes:Buffer.byteLength(JSON.stringify(native)),origin:(message.originalProviderFields as {activationId?:string}|undefined)?.activationId}}
 private historyBudget(bytes:number,count:number){if(bytes>this.config.maxHistoryBytes||count>=this.config.maxHistoryMessages)throw contextLimit()}
 private returnedAfter(messages:AgentMessage[],index:number){const returned=new Set<string>();for(let next=index+1;next<messages.length&&messages[next]!.role!=='assistant';next++){const message=messages[next]!;if(message.role==='toolResult')returned.add(message.toolCallId)}return returned}
 private async recoverCall(message:AssistantMessage,call:ToolCall,origins:ReadonlyMap<AgentMessage,string>):Promise<AgentMessage>{
  const activation=origins.get(message);
  if(!activation)throw new LlmActivationError('thread_error','INFERENCE_UNRESOLVED_TOOL','thread.history');
  const fact=await this.step('persistence_error','attempts.probe',()=>this.ctx.attempts.probe({lineage:this.ctx.attempts.authority().lineage,operationId:activation+'/undispatched/'+call.id,attempt:0}));
  if(fact.status!=='found'||fact.record.status!=='settled'||(fact.record.result as {status?:string})?.status!=='not_dispatched')throw new LlmActivationError('thread_error','INFERENCE_UNRESOLVED_TOOL','thread.history');
  return {role:'toolResult',toolCallId:call.id,toolName:call.name,content:[{type:'text',text:JSON.stringify({status:'not_dispatched',reason:'cancelled'})}],details:{status:'not_dispatched'},isError:true,timestamp:0};
 }
 private async reconcile(messages:AgentMessage[],origins:ReadonlyMap<AgentMessage,string>):Promise<AgentMessage[]>{const recovered:AgentMessage[]=[];for(let index=0;index<messages.length;index++){const message=messages[index]!;recovered.push(message);if(message.role!=='assistant')continue;const returned=this.returnedAfter(messages,index);for(const call of message.content){if(call.type!=='toolCall'||returned.has(call.id))continue;recovered.push(await this.recoverCall(message,call,origins))}}return recovered}
 private async history():Promise<AgentMessage[]>{
  return this.withView(view=>this.step('thread_error','thread.history',async()=>{
   const messages:AgentMessage[]=[],origins=new Map<AgentMessage,string>();let cursor:string|undefined,bytes=0;
   do{
    const page=await this.ctx.threads.page(this.thread.id,this.scope(),view,{cursor,maxItems:this.config.maxPageItems,maxResponseBytes:this.config.maxPageBytes});
    for(const row of page.items){
     if(row.role==='inference_event')continue;
     const decoded=await this.decodeMessage(row.payload);
     bytes+=decoded.bytes;this.historyBudget(bytes,messages.length);
     messages.push(decoded.native);
     if(decoded.origin)origins.set(decoded.native,decoded.origin);
    }
    cursor=page.next;
   }while(cursor);
   return this.reconcile(messages,origins);
  }));
 }
 private async percepts(contexts:readonly StoredValue[],paths:string[]){
  if(!this.config.perceptions.length)return;
  const logicalTime=this.context.logicalTime;
  const selected=await this.step('perception_error','perception.collect',()=>this.ctx.perceptions.acquire(this.config.perceptions,this.context));
  this.held.push(selected);
  for(let page=0;page<this.config.maxPerceptionPages;page++){
   const scope={actorId:this.context.actorId,momentId:this.context.momentId,threadId:this.thread.id,logicalTime,reason:this.intent.input};
   const batch=await this.step('perception_error','perception.collect',()=>selected.value.collect(scope,{maxItems:this.config.maxPageItems,maxResponseBytes:this.config.maxPageBytes},this.context));
   if(!batch.reads.length)return;
   const inputs:PerceptionInput[]=[];
   for(const read of batch.reads){
    if(!read.page.items.length)continue;
    const texts=await this.step('perception_error','perception.material',()=>Promise.all(read.page.items.map(async item=>JSON.stringify({source:read.sourceId,id:item.id,kind:item.kind,provenance:item.provenance,material:await this.readValue(item.material)}))));
    const mounted=await this.step('setup_error','context.freeze',()=>this.ctx.contextMount.freeze(texts,{...this.context,operationId:this.context.operationId+'/perception/'+page+'/'+read.sourceId}));
    this.held.push(mounted);paths.push(...mounted.value);
    const linked=await this.step('persistence_error','artifact.stage',()=>Promise.all(texts.map(text=>prepareValue(text,16384,this.ctx.artifacts,this.context.signal))));
    const native={role:'user',content:JSON.stringify({source:read.sourceId,percepts:read.page.items,paths:mounted.value}),timestamp:Date.now()};
    inputs.push({sourceId:read.sourceId,perceptIds:read.page.items.map(item=>item.id),message:await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(this.context.operationId+'/perception/'+page+'/'+read.sourceId,'user',native)),contexts:[...contexts,...linked]});
   }
   await this.transaction('perception/'+page,tx=>this.step('perception_error','perception.commit',()=>selected.value.commit(tx,batch,inputs)));
   if(!batch.reads.some(read=>read.page.hasMore))return;
  }
  throw new LlmActivationError('perception_error','PERCEPTION_PAGE_BUDGET','perception.collect');
 }
 private async input(sections:readonly string[],paths:readonly string[],contexts:readonly StoredValue[]){
  const consumerId='driver:'+this.config.name;
  const previous=await this.withView(view=>this.step('thread_error','thread.consumer',()=>this.ctx.threads.consumer(this.thread.id,consumerId,view)));
  if((previous?.cursor as {operationId?:string})?.operationId===this.context.operationId)return;
  const native={role:'user',content:[...sections,'冻结上下文：'+paths.join(', '),'本次输入：'+JSON.stringify(this.intent.input)].join('\n'),timestamp:Date.now()};
  const message=await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(this.context.operationId+'/input','user',native));
  await this.transaction('input',tx=>this.step('thread_error','thread.appendInput',()=>this.ctx.threads.appendInput(tx,{threadId:this.thread.id,expectedConsumer:previous??null,nextConsumer:{consumerId,cursor:{operationId:this.context.operationId}},message,contexts})));
 }
 private async event(frame:SequencedInferenceEvent,attemptIds:readonly string[]){
  const event=frame.event;let role='inference_event',content:unknown=event;
  if(event.type==='message_end'){role=event.message.role;content=event.message}
  else if(event.type==='message_update')content={type:event.type,nativeEventType:event.assistantMessageEvent.type,attemptIds};
  const id=this.context.operationId+'/event/'+frame.sequence;
  const message=await this.step('persistence_error','thread.prepare',()=>this.prepareMessage(id,role,content as Json,{activationId:this.context.operationId,sequence:frame.sequence,attemptIds:[...attemptIds]}));
  await this.transaction('event/'+frame.sequence,tx=>this.step('thread_error','thread.append',()=>this.ctx.threads.append(tx,this.thread.id,message)));
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
 private async open(){this.thread=await this.withView(view=>this.step('thread_error','thread.open',()=>this.ctx.threads.open(this.context.actorId,this.context.momentId,view)))}
 private async prepareContext():Promise<PreparedContext>{
  const prompts=await this.step('setup_error','prompts',()=>this.ctx.prompts.acquire(this.context));
  this.held.push(prompts);
  const texts=[...prompts.value.contexts,JSON.stringify({kind:'activation',actorId:this.context.actorId,momentId:this.context.momentId,logicalTime:this.context.logicalTime,input:this.intent.input})];
  const frozen=await this.step('setup_error','context.freeze',()=>this.ctx.contextMount.freeze(texts,this.context));
  this.held.push(frozen);
  const contexts=await this.step('persistence_error','artifact.stage',()=>Promise.all(texts.map(text=>prepareValue(text,16384,this.ctx.artifacts,this.context.signal))));
  const paths=[...frozen.value];
  await this.percepts(contexts,paths);
  await this.input(prompts.value.sections,paths,contexts);
  const history=await this.history();
  const binding=await this.step('setup_error','actor-tools',()=>this.ctx.actorTools.bind(this.context,prompts.value.tools));
  this.held.push(binding);
  const selected=await this.step('provider_error','model.prepare',()=>this.ctx.modelProviders.prepare(this.config,this.context));
  this.held.push(selected);
  const runtime=await this.step('setup_error','runtime.acquire',()=>this.ctx.agentRuntimes.acquire(this.config.runtime));
  this.held.push(runtime);
  const extensions=await this.step('setup_error','runtime.acquire',()=>acquireExtensions(this.ctx,this.context));
  this.held.push(extensions);
  const sink=new OrderedInferenceSink({maxPendingEvents:this.config.maxPendingEvents,maxPendingBytes:this.config.maxPendingBytes},frame=>this.event(frame,selected.value.attemptIds??[]),receipt=>this.undispatched(receipt),error=>this.controller.abort(error));
  return {binding,selected,runtime,extensions,sink,history};
 }
 private async runRuntime(runtime:Lease<AgentRuntime>,request:InferenceRequest,sink:InferenceEventSink,extensions:InferenceExtensions):Promise<InferenceOutcome>{
  try{return await runtime.value.run(request,this.context,sink,extensions)}
  catch(error){if(error instanceof LlmActivationError)throw error;throw classified(runtimeSetup.has(codeOf(error))?'setup_error':'internal_error','runtime.run',error)}
 }
 private async infer(prepared:PreparedContext):Promise<ActivationResult>{
  const {binding,selected,runtime,extensions,sink,history}=prepared;
  const actionReceipts={read:()=>this.step('persistence_error','actions.receipts',()=>binding.value.actionReceipts.read())};
  const outcome=await this.runRuntime(runtime,{threadId:this.thread.id,interactionId:this.thread.interactionId,history,prompts:[],tools:binding.value.tools,actionReceipts,selection:selected.value,budget:{maxRequests:this.config.maxRequests,maxToolCalls:this.config.maxToolCalls},requiredActions:this.config.requiredActions},sink,extensions);
  for(const item of outcome.actionReceipts)for(const operationId of item.receipt.requiredOperations??[])this.operations.add(operationId);
  return {status:outcome.status,value:{reason:outcome.reason},evidence:outcome as unknown as Json,requiredOperations:[...this.operations]};
 }
 private failureReason(failure:LlmActivationError):LlmActivationReason{
  const unknown=failure.reason==='persistence_unknown'||failure.reason==='transport_unknown';
  return unknown||failure.reason==='context_limit'||!aborted(failure,this.context.signal)?failure.reason:'cancelled';
 }
 private failureEvidence(failure:LlmActivationError):Json{
  const code=codeOf(failure.cause);
  return {error:String(failure.cause??failure),code:failure.code,stage:failure.stage,committed:code==='SQLITE_SEGMENT_FAILED'?true:code==='SQLITE_COMMIT_UNKNOWN'?null:false,receipt:(failure.cause as {receipt?:Json})?.receipt??null};
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
