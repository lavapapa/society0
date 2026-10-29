import {createAssistantMessageEventStream,type AssistantMessage,type Model,type Api} from '@earendil-works/pi-ai';
import type {ModelProvider,ModelSelectionRequest,ResolvedModelSelection} from '../contracts/inference.js';
import type {Invocation,Lease} from '../contracts/common.js';
export const replayModel:Model<Api>={id:'replay',name:'Explicit replay',api:'openai-completions',provider:'replay',baseUrl:'replay:',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256};
class ReplaySelection {
 readonly value:ResolvedModelSelection;private cursor=0;private released=false;
 constructor(private fixture:readonly AssistantMessage[],request:ModelSelectionRequest,private context:Invocation){const model=Object.freeze({...replayModel,id:request.model});this.value={provider:request.provider,registrationId:'replay/v1',model,contextWindow:model.contextWindow,options:{maxRetries:0,transport:'sse',maxTokens:256},streamFn:(_model,_context,options)=>this.stream(options?.signal)}}
 private message(signal?:AbortSignal){if(this.released)throw Error('REPLAY_LEASE_RELEASED');const next=this.fixture[this.cursor++];if(!next)throw Error('REPLAY_FIXTURE_EXHAUSTED');const message=structuredClone(next);if(signal?.aborted||this.context.signal.aborted){message.stopReason='aborted';message.errorMessage='Replay cancelled'}return message}
 private stream(signal?:AbortSignal){const message=this.message(signal),stream=createAssistantMessageEventStream();stream.push({type:'start',partial:message});if(message.stopReason==='error'||message.stopReason==='aborted')stream.push({type:'error',reason:message.stopReason,error:message});else{if(message.stopReason==='pending')throw Error('REPLAY_PENDING_FIXTURE');stream.push({type:'done',reason:message.stopReason,message})}stream.end();return stream}
 lease():Lease<ResolvedModelSelection>{return {value:this.value,release:async()=>{this.released=true}}}
}
export class ReplayProvider implements ModelProvider {
 private closed=false;constructor(private fixtures:Readonly<Record<string,readonly AssistantMessage[]>>){}
 async prepare(request:ModelSelectionRequest,context:Invocation):Promise<Lease<ResolvedModelSelection>>{if(this.closed)throw Error('REPLAY_CLOSED');const fixture=this.fixtures[request.model];if(!fixture)throw Error('REPLAY_FIXTURE_MISSING');return new ReplaySelection(fixture,request,context).lease()}
 async close(){this.closed=true}
}
