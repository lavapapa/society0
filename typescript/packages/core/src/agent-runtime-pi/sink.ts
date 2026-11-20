import type {AgentEvent} from '@earendil-works/pi-agent-core';
import type {InferenceEventSink,InferenceSinkLimits,SequencedInferenceEvent,UndispatchedToolReceipt} from '../contracts/inference.js';
import type {Invocation} from '../contracts/common.js';
export class OrderedInferenceSink implements InferenceEventSink {
 private tail:Promise<void>=Promise.resolve();private sequence=0;private pending=0;private bytes=0;private failure?:Error;private accepting=true;
 constructor(readonly limits:InferenceSinkLimits,private persist:(frame:SequencedInferenceEvent,context:Invocation)=>Promise<void>,private persistUndispatched:(receipt:UndispatchedToolReceipt,context:Invocation)=>Promise<void>,private abort:(error:Error)=>void){for(const n of Object.values(limits))if(!Number.isSafeInteger(n)||n<1)throw Error('INFERENCE_SINK_LIMIT')}
 /** 中止原因包装为 INFERENCE_SINK_FAILED（cause 为原错误）：接到激活信号上的 abort 不得让 failSave 把 sink 自身故障误判为外部取消。 */
 private fail(cause:unknown){if(!this.failure){this.failure=cause instanceof Error?cause:Error(String(cause));this.abort(Object.assign(Error('INFERENCE_SINK_FAILED',{cause:this.failure}),{code:'INFERENCE_SINK_FAILED'}))}return this.failure}
 append(event:AgentEvent,context:Invocation):Promise<void>{
  let snapshot:AgentEvent,size:number;try{snapshot=structuredClone(event);size=Buffer.byteLength(JSON.stringify(snapshot))}catch(error){return this.reject(this.fail(error))}
  if(!this.accepting||this.failure||this.pending>=this.limits.maxPendingEvents||this.bytes+size>this.limits.maxPendingBytes)return this.reject(this.fail(Object.assign(Error('INFERENCE_SINK_BUDGET'),{code:'INFERENCE_SINK_BUDGET'})));
  const frame={sequence:++this.sequence,event:snapshot};this.pending++;this.bytes+=size;
  const work=this.tail.then(()=>this.persist(frame,context)).finally(()=>{this.pending--;this.bytes-=size});void work.catch(error=>this.fail(error));this.tail=work;return work;
 }
 private reject(error:Error){const work=Promise.reject<void>(error);void work.catch(()=>{});return work}
 async appendUndispatched(receipt:UndispatchedToolReceipt,context:Invocation){await this.tail.catch(()=>{});await this.persistUndispatched(receipt,context)}
 async drain(){this.accepting=false;await this.tail;if(this.failure)throw this.failure}
 diagnostics(){return {pending:this.pending,bytes:this.bytes,sequence:this.sequence,accepting:this.accepting,failed:!!this.failure}}
}
