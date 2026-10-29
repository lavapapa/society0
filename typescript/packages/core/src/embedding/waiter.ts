import type {EmbeddingUse,EmbeddingOrigin} from '../contracts/cognition.js';
export interface SharedVector {vector:readonly number[];origin:EmbeddingOrigin}
export class EmbeddingWaiter {
 private result=Promise.withResolvers<readonly EmbeddingUse[]>();readonly promise=this.result.promise;private done=false;private uses:EmbeddingUse[];private removers:(()=>void)[]=[];
 constructor(private remaining:number,private signal:AbortSignal,private releaseBudget:()=>void){this.uses=new Array(remaining);signal.addEventListener('abort',this.abort,{once:true});if(signal.aborted)this.abort();else if(!remaining){this.cleanup();this.result.resolve([])}}
 private abort=()=>{this.cleanup();this.result.reject(this.signal.reason??Error('EMBEDDING_WAITER_CANCELLED'))};
 private cleanup(){if(this.done)return;this.done=true;this.releaseBudget();this.signal.removeEventListener('abort',this.abort);for(const remove of this.removers)remove();this.removers=[]}
 attach(remove:()=>void){if(this.done)remove();else this.removers.push(remove)}
 receive(inputIndex:number,reused:EmbeddingUse['reused'],value:SharedVector|Error){if(this.done)return;if(value instanceof Error){this.cleanup();this.result.reject(value);return}this.uses[inputIndex]={inputIndex,...value,reused};if(!--this.remaining){this.cleanup();this.result.resolve(this.uses)}}
}
