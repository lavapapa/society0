/** 物理请求结局；与contracts/inference.ts的ModelAttemptSettlement.status同一集合。 */
export type PhysicalStatus='response'|'failed'|'not_dispatched'|'unknown';
const preconnect=new Set(['ECONNREFUSED','ENOTFOUND','EAI_AGAIN','EAI_NONAME','ENETUNREACH','EHOSTUNREACH','UND_ERR_CONNECT_TIMEOUT']),rejected=new Set([400,401,403,404,422]);
function codes(error:unknown,depth=0):string[]{if(!error||typeof error!=='object'||depth>4)return [];const item=error as {code?:unknown;cause?:unknown;errors?:unknown[]};return [...(typeof item.code==='string'?[item.code]:[]),...codes(item.cause,depth+1),...(item.errors??[]).flatMap(value=>codes(value,depth+1))]}
/** 单个物理请求的fetch相位观察；只统计目标端点，其他调用记为辅助调用；结论只依据记录到的事实。 */
export class TransportObserver {
 phase:'idle'|'dispatching'|'headers'|'complete'='idle';httpStatus:number|null=null;calls=0;auxiliaryCalls=0;abortedBeforeDispatch=false;private error:unknown;
 constructor(private endpoint:string){}
 /** 同一次selection内重试时，只清掉上一个物理attempt的相位与错误，保留辅助调用统计。 */
 reset(){this.phase='idle';this.httpStatus=null;this.calls=0;this.abortedBeforeDispatch=false;this.error=undefined}
 private target(input:Parameters<typeof fetch>[0]){const url=input instanceof Request?input.url:String(input);return url.startsWith(this.endpoint)}
 private body(response:Response,maxBytes:number,code:string){if(!response.body){this.phase='complete';return response}let bytes=0;const body=response.body.pipeThrough(new TransformStream<Uint8Array,Uint8Array>({transform:(chunk,output)=>{bytes+=chunk.length;if(bytes>maxBytes)throw Object.assign(Error(code),{code});output.enqueue(chunk)},flush:()=>{this.phase='complete'}}));return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers})}
 fetch(base:typeof fetch,maxBytes:number,code:string):typeof fetch{return async(input,init)=>{if(!this.target(input)){this.auxiliaryCalls++;return base(input,init)}const signal=init?.signal??(input instanceof Request?input.signal:undefined);if(signal?.aborted){this.abortedBeforeDispatch=true;signal.throwIfAborted()}this.calls++;this.phase='dispatching';let response:Response;try{response=await base(input,{...init,redirect:'error'})}catch(error){this.error=error;throw error}this.phase='headers';this.httpStatus=response.status;return this.body(response,maxBytes,code)}}
 private dispatched():PhysicalStatus{return codes(this.error).some(code=>preconnect.has(code))?'not_dispatched':'unknown'}
 private answered():PhysicalStatus{const status=this.httpStatus!,done=this.phase==='complete';if(status>=200&&status<300)return done?'response':'unknown';return rejected.has(status)&&done?'failed':'unknown'}
 classify(terminal:boolean):PhysicalStatus{if(terminal)return 'response';if(this.abortedBeforeDispatch)return 'not_dispatched';if(this.phase==='dispatching')return this.dispatched();return this.answered()}
 cause(){if(this.abortedBeforeDispatch)return null;if(this.calls===0)return 'MODEL_TRANSPORT_UNOBSERVED';const error=this.error as {code?:string;message?:string}|undefined;if(error)return String(error.code??error.message??error);return this.calls>1&&this.httpStatus===null?'MODEL_TRANSPORT_MULTIPLE':null}
}
