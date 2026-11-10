import type {Context} from '@society0/host';
import type {AgentTool} from '@earendil-works/pi-agent-core';
import type {ActivationContext} from '../contracts/runtime.js';
import type {ActionExecutionContext,ActionReceipt} from '../contracts/domain.js';
import type {ExplorationSession} from '../contracts/exploration.js';
import type {Json} from '../contracts/common.js';
import {ToolReceipts,type ReceiptLimits} from './receipts.js';
import { assertWellFormed } from '../contracts/text.js';
import { recoverableActionCodes } from './outcome.js';
import { parseTarget,targetText,emptyOfferFold,availabilitySummary,availabilityText,availabilityDetails,type AvailabilitySummary,type OfferFoldState,type OfferLimits,type OfferSet } from '../contracts/affordance.js';
import { suggestedCallsFromOffers } from '../actions/suggest.js';
import { formatToolResult } from './format.js';
import { deliveredKeys, offerDelivery, DRIVER_OFFER_LIMITS } from '../driver-llm/offers.js';
export const boundActions=Symbol('actor-tools.actions');export const boundSession=Symbol('actor-tools.session');
export type SelectedActions=Awaited<ReturnType<Context['actions']['acquire']>>['value'];
export type BoundContext=ActionExecutionContext&{[boundSession]?:ExplorationSession;[boundActions]?:SelectedActions};
type Failure={code?:string;requiredOperations?:readonly string[];artifacts?:ActionReceipt['artifacts'];storage?:boolean};
function withNormalization(receipt:ActionReceipt,note:string):ActionReceipt{
 const value=receipt.value;
 if(value===null||typeof value!=='object'||Array.isArray(value))return receipt;
 return {...receipt,value:{...(value as Readonly<Record<string,Json>>),targetNormalization:note}};
}
export class BoundToolExecution {
 readonly receipts:ToolReceipts;private count=0;private offers:OfferFoldState;private delivered:Set<string>;
 constructor(private ctx:Context,private context:ActivationContext,private config:ReceiptLimits,private session:ExplorationSession,private actions:SelectedActions,offers:OfferFoldState=emptyOfferFold(),private limits:OfferLimits=DRIVER_OFFER_LIMITS){this.receipts=new ToolReceipts(ctx,context,config);this.offers=offers;this.delivered=new Set(deliveredKeys(offers))}
 tool(name:string,action:string,parameters:AgentTool['parameters']):AgentTool{return {name,label:name,description:'通过当前主体Actions执行 '+action,parameters,execute:(id,args,signal)=>this.invoke(id,action,args as Json,signal)}}
 private invocation(toolCallId:string,signal?:AbortSignal){assertWellFormed(toolCallId,'toolCall.id','INFERENCE_MODEL_TEXT_NOT_WELL_FORMED');if(++this.count>this.config.maxToolCalls)throw Error('TOOL_CALL_BUDGET');return {...this.context,operationId:this.context.operationId+'/tool/'+toolCallId,signal:AbortSignal.any([this.context.signal,...(signal?[signal]:[])]),deliveredOffers:this.delivered,[boundSession]:this.session,[boundActions]:this.actions}}
 async act(toolCallId:string,name:string,input:Json,target:string|undefined,signal?:AbortSignal){return this.invoke(toolCallId,name,input,signal,target,true)}
 async invoke(toolCallId:string,name:string,input:Json,signal?:AbortSignal,target?:string,deliver=false){const invocation=this.invocation(toolCallId,signal);let receipt:ActionReceipt,normalized:{target:string;note?:string}|undefined;try{if(target!==undefined)normalized=await this.normalizeTarget(name,target);const effective=normalized?normalized.target:target;const ref=effective===undefined?undefined:parseTarget(effective);receipt=await this.actions.execute(name,input,invocation,ref)}catch(error){const failure=error as Failure;if(failure.code&&(recoverableActionCodes.has(failure.code)||failure.code==='ACTION_UNAVAILABLE')){const rejected=failure.code==='ACTION_TARGET_INVALID'?await this.targetFailure(invocation.operationId,failure,name,target,normalized?.note):this.rejectionReceipt(invocation.operationId,error,failure,normalized?.note);return this.result(name,await this.save(toolCallId,name,rejected),deliver)}return this.failed(toolCallId,name,invocation.operationId,error)}const durable=await this.save(toolCallId,name,normalized?.note?withNormalization(receipt,normalized.note):receipt);return this.result(name,durable,deliver)}
 /** 只补当前行动下唯一能推出的 target；任何歧义都保持原样交给 Core 拒绝。 */
 private async normalizeTarget(name:string,target:string):Promise<{target:string;note?:string}>{
  if(target.includes(':'))return {target};
  let card:Awaited<ReturnType<Context['actions']['describe']>>;
  try{card=await this.ctx.actions.describe(name)}catch{return {target}}
  if(!card?.targets?.length)return {target};
  const set=await this.currentOffers();
  if(!set)return {target};
  const candidates=set.offers.filter(offer=>offer.action===name&&offer.target);
  const byId=candidates.filter(offer=>offer.target!.id===target);
  if(byId.length===1)return {target:targetText(byId[0].target!),note:`已补全：${target}→${targetText(byId[0].target!)}`};
  if(byId.length>1)return {target};
  const byKind=candidates.filter(offer=>offer.target!.kind===target);
  if(byKind.length===1)return {target:targetText(byKind[0].target!),note:`已补全：${target}→${targetText(byKind[0].target!)}`};
  return {target};
 }
 private async currentOffers():Promise<OfferSet|undefined>{
  try{const view=await this.ctx.database.snapshot({maxAgeMs:30000});try{const scope={actorId:this.context.actorId,momentId:this.context.momentId,logicalTime:this.context.logicalTime,query:null};return await this.ctx.actions.offers(scope,view,this.limits)}finally{await view.close()}}catch{return undefined}
 }
 /** parseTarget 失败也给出原因、当前候选与可整串照抄的建议；候选计算失败时仍保留说明。 */
 private async targetFailure(operationId:string,failure:Failure,name:string,target:string|undefined,normalization?:string):Promise<ActionReceipt>{
  const set=await this.currentOffers();
  let summary:AvailabilitySummary|undefined,card:Awaited<ReturnType<Context['actions']['describe']>>,example:Json|undefined;
  if(set)summary=availabilitySummary(set.offers,name,undefined,5,set.omitted);
  try{card=await this.ctx.actions.describe(name)}catch{}
  try{example=(await this.ctx.actions.inputHelp(name))?.example}catch{}
  const reason=target===undefined?'缺少 target':card&&!card.targets?`该行动不接受 target（收到 ${String(target)}）`:`target 需形如 kind:id（收到 ${String(target)}）${card?.targets?.length?`；target.kind 应为 ${card.targets.join(', ')}`:''}`;
  const issue=target===undefined?'missing':card&&!card.targets?'unexpected':undefined;
  const suggested=suggestedCallsFromOffers(set?.offers??[],name,{...(issue?{issue}:{}),...(example!==undefined?{example}:{})});
  return {operationId,status:'rejected',value:{code:'ACTION_TARGET_INVALID',message:`${reason}。${summary?availabilityText(summary):'当前上下文没有可用行动'}`,...(summary?{details:availabilityDetails(summary)}:{}),...(suggested.length?{suggestedCall:suggested as unknown as Json}:{}),...(normalization?{targetNormalization:normalization}:{}),action:name,...(target!==undefined?{target:String(target)}:{})},original:null,artifacts:failure.artifacts??[],requiredOperations:failure.requiredOperations??[]};
 }
 private async delivery(){const view=await this.ctx.database.snapshot({maxAgeMs:30000});try{const scope={actorId:this.context.actorId,momentId:this.context.momentId,logicalTime:this.context.logicalTime,query:null};const value=await offerDelivery(this.ctx.actions,scope,view,this.offers,this.limits);this.offers=value.fold;this.delivered=new Set(deliveredKeys(value.fold));return value}finally{await view.close()}}
 private async result(name:string,durable:ActionReceipt,deliver=false){const base={content:[{type:'text' as const,text:formatToolResult(name,durable)}],details:{action:name,operationId:durable.operationId,status:durable.status,...(durable.status!=='completed'?{code:(durable.value as {code?:string}|null)?.code}:{}),...(durable.replayed?{replayed:true}:{}),...(durable.inputMismatch?{inputMismatch:true}:{})}};if(!deliver)return base;const value=await this.delivery();const text=value.text?base.content[0].text+'\n'+value.text:base.content[0].text;return {content:[{type:'text' as const,text}],details:{...base.details,...(value.record?{offers:value.record}:{})}}}
 private rejectionReceipt(operationId:string,error:unknown,failure:Failure,normalization?:string):ActionReceipt{return {operationId,status:'rejected',value:{code:failure.code??'ACTION_ERROR',message:String(error),...(normalization?{targetNormalization:normalization}:{})},original:null,artifacts:failure.artifacts??[],requiredOperations:failure.requiredOperations??[]}}
 private async save(id:string,name:string,receipt:ActionReceipt){try{return await this.receipts.save(id,name,receipt)}catch(error){if(this.storageUnknown(error as Failure))throw Object.assign(Error('INFERENCE_PERSISTENCE_UNKNOWN'),{code:'INFERENCE_PERSISTENCE_UNKNOWN',cause:error});throw Object.assign(Error('INFERENCE_PERSISTENCE_ERROR'),{code:'INFERENCE_PERSISTENCE_ERROR',cause:error})}}
 private unknown(failure:Failure){return failure.code==='ACTION_ATTEMPT_UNKNOWN'&&!this.storageUnknown(failure)}
 private storageUnknown(failure:Failure){return failure.storage===true||failure.code==='SQLITE_COMMIT_UNKNOWN'||failure.code==='SQLITE_SEGMENT_FAILED'}
 private failureReceipt(operationId:string,error:unknown,unknown:boolean):ActionReceipt{const failure=error as Failure;return {operationId,status:'incomplete',value:{code:failure.code??'ACTION_ERROR',message:String(error),externalUnknown:unknown,persistenceUnknown:this.storageUnknown(failure)},original:null,artifacts:failure.artifacts??[],requiredOperations:failure.requiredOperations??[]}}
 private async failed(id:string,name:string,operationId:string,error:unknown):Promise<never>{const failure=error as Failure,unknown=this.unknown(failure),receipt=this.failureReceipt(operationId,error,unknown);let persistence:unknown;try{await this.save(id,name,receipt)}catch(saveError){persistence=saveError}if(unknown)throw Object.assign(Error('INFERENCE_EXTERNAL_UNKNOWN'),{code:'INFERENCE_EXTERNAL_UNKNOWN',cause:error,persistence});if(this.storageUnknown(failure))throw Object.assign(Error('INFERENCE_PERSISTENCE_UNKNOWN'),{code:'INFERENCE_PERSISTENCE_UNKNOWN',cause:error,persistence});if(persistence)throw persistence;throw error}
}
