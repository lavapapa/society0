import type {AgentEvent} from '@earendil-works/pi-agent-core';
import type {InferenceEventSink,InferenceSinkLimits,SequencedInferenceEvent,UndispatchedToolReceipt} from '../contracts/inference.js';
import type {Invocation} from '../contracts/common.js';
export class OrderedInferenceSink implements InferenceEventSink {
 private tail:Promise<void>=Promise.resolve();private sequence=0;private pending=0;private bytes=0;private failure?:Error;private accepting=true;
 constructor(readonly limits:InferenceSinkLimits,private persist:(frame:SequencedInferenceEvent,context:Invocation)=>Promise<void>,private persistUndispatched:(receipt:UndispatchedToolReceipt,context:Invocation)=>Promise<void>,private abort:(error:Error)=>void){for(const n of Object.values(limits))if(!Number.isSafeInteger(n)||n<1)throw Error('INFERENCE_SINK_LIMIT')}
 private fail(cause:unknown){this.failure??=cause instanceof Error?cause:Error(String(cause));this.abort(this.failure);return this.failure}
 append(event:AgentEvent,context:Invocation):Promise<void>{
  let snapshot:AgentEvent,size:number;try{snapshot=structuredClone(event);size=Buffer.byteLength(JSON.stringify(snapshot))}catch(error){return this.reject(this.fail(error))}
  if(!this.accepting||this.failure||this.pending>=this.limits.maxPendingEvents||this.bytes+size>this.limits.maxPendingBytes)return this.reject(this.fail(Error('INFERENCE_SINK_BUDGET')));
  const frame={sequence:++this.sequence,event:snapshot};this.pending++;this.bytes+=size;
  const work=this.tail.then(()=>this.persist(frame,context)).finally(()=>{this.pending--;this.bytes-=size});void work.catch(error=>this.fail(error));this.tail=work;return work;
 }
 private reject(error:Error){const work=Promise.reject<void>(error);void work.catch(()=>{});return work}
 async appendUndispatched(receipt:UndispatchedToolReceipt,context:Invocation){await this.tail.catch(()=>{});await this.persistUndispatched(receipt,context)}
 async drain(){this.accepting=false;await this.tail;if(this.failure)throw this.failure}
 diagnostics(){return {pending:this.pending,bytes:this.bytes,sequence:this.sequence,accepting:this.accepting,failed:!!this.failure}}
}
