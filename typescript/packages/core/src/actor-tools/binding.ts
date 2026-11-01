import type {Context} from '@society0/host';
import type {AgentTool} from '@earendil-works/pi-agent-core';
import type {ActivationContext} from '../contracts/runtime.js';
import type {ActionExecutionContext,ActionReceipt} from '../contracts/domain.js';
import type {ExplorationSession} from '../contracts/exploration.js';
import type {Json} from '../contracts/common.js';
import {ToolReceipts,type ReceiptLimits} from './receipts.js';
import { assertWellFormed } from '../contracts/text.js';
export const boundActions=Symbol('actor-tools.actions');export const boundSession=Symbol('actor-tools.session');
export type SelectedActions=Awaited<ReturnType<Context['actions']['acquire']>>['value'];
export type BoundContext=ActionExecutionContext&{[boundSession]?:ExplorationSession;[boundActions]?:SelectedActions};
type Failure={code?:string;requiredOperations?:readonly string[];artifacts?:ActionReceipt['artifacts'];storage?:boolean};
export class BoundToolExecution {
 readonly receipts:ToolReceipts;private count=0;
 constructor(private ctx:Context,private context:ActivationContext,private config:ReceiptLimits,private session:ExplorationSession,private actions:SelectedActions){this.receipts=new ToolReceipts(ctx,context,config)}
 tool(name:string,action:string,parameters:AgentTool['parameters']):AgentTool{return {name,label:name,description:'通过当前主体Actions执行 '+action,parameters,execute:(id,args,signal)=>this.invoke(id,action,args as Json,signal)}}
 private invocation(toolCallId:string,signal?:AbortSignal){assertWellFormed(toolCallId,'toolCall.id','INFERENCE_MODEL_TEXT_NOT_WELL_FORMED');if(++this.count>this.config.maxToolCalls)throw Error('TOOL_CALL_BUDGET');return {...this.context,operationId:this.context.operationId+'/tool/'+toolCallId,signal:AbortSignal.any([this.context.signal,...(signal?[signal]:[])]),[boundSession]:this.session,[boundActions]:this.actions}}
 async invoke(toolCallId:string,name:string,input:Json,signal?:AbortSignal){const invocation=this.invocation(toolCallId,signal);let receipt:ActionReceipt;try{receipt=await this.actions.execute(name,input,invocation)}catch(error){return this.failed(toolCallId,name,invocation.operationId,error)}const durable=await this.save(toolCallId,name,receipt);return {content:[{type:'text' as const,text:JSON.stringify(durable.value)}],details:{action:name,operationId:durable.operationId,status:durable.status}}}
 private async save(id:string,name:string,receipt:ActionReceipt){try{return await this.receipts.save(id,name,receipt)}catch(error){if(this.storageUnknown(error as Failure))throw Object.assign(Error('INFERENCE_PERSISTENCE_UNKNOWN'),{code:'INFERENCE_PERSISTENCE_UNKNOWN',cause:error});throw Object.assign(Error('INFERENCE_PERSISTENCE_ERROR'),{code:'INFERENCE_PERSISTENCE_ERROR',cause:error})}}
 private unknown(failure:Failure){return failure.code==='ACTION_ATTEMPT_UNKNOWN'&&!this.storageUnknown(failure)}
 private storageUnknown(failure:Failure){return failure.storage===true||failure.code==='SQLITE_COMMIT_UNKNOWN'||failure.code==='SQLITE_SEGMENT_FAILED'}
 private failureReceipt(operationId:string,error:unknown,unknown:boolean):ActionReceipt{const failure=error as Failure;return {operationId,status:'incomplete',value:{code:failure.code??'ACTION_ERROR',message:String(error),externalUnknown:unknown,persistenceUnknown:this.storageUnknown(failure)},original:null,artifacts:failure.artifacts??[],requiredOperations:failure.requiredOperations??[]}}
 private async failed(id:string,name:string,operationId:string,error:unknown):Promise<never>{const failure=error as Failure,unknown=this.unknown(failure),receipt=this.failureReceipt(operationId,error,unknown);let persistence:unknown;try{await this.save(id,name,receipt)}catch(saveError){persistence=saveError}if(unknown)throw Object.assign(Error('INFERENCE_EXTERNAL_UNKNOWN'),{code:'INFERENCE_EXTERNAL_UNKNOWN',cause:error,persistence});if(this.storageUnknown(failure))throw Object.assign(Error('INFERENCE_PERSISTENCE_UNKNOWN'),{code:'INFERENCE_PERSISTENCE_UNKNOWN',cause:error,persistence});if(persistence)throw persistence;throw error}
}
