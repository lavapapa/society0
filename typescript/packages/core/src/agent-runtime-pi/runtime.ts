import type {Invocation} from '../contracts/common.js';
import type {AgentRuntime,InferenceRequest,InferenceEventSink,InferenceExtensions,InferenceOutcome} from '../contracts/inference.js';
import {PiActivation} from './activation.js';
export {completedActions} from './facts.js';
export class PiRuntime implements AgentRuntime {
 private active=new Map<Promise<InferenceOutcome>,AbortController>();private closing=false;private droppedObservers=0;
 run(request:InferenceRequest,context:Invocation,sink:InferenceEventSink,extensions:InferenceExtensions):Promise<InferenceOutcome>{if(this.closing)return Promise.reject(Error('INFERENCE_RUNTIME_CLOSED'));const controller=new AbortController();const activation=new PiActivation(request,{...context,signal:AbortSignal.any([context.signal,controller.signal])},sink,extensions,controller,()=>{this.droppedObservers++});const work=activation.run();this.active.set(work,controller);void work.then(()=>this.active.delete(work),()=>this.active.delete(work));return work}
 async drain(reason:string){this.closing=true;for(const controller of this.active.values())controller.abort(Error(reason));await Promise.allSettled([...this.active.keys()])}
 diagnostics(){return {active:this.active.size,closing:this.closing,droppedObservers:this.droppedObservers}}
}
