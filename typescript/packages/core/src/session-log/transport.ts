/** 单个物理请求的 fetch 观察：只看目标端点，记 HTTP 状态与传输错误，限制回包字节；其他调用记为辅助调用。 */
export class TransportObserver {
 httpStatus:number|null=null;calls=0;auxiliaryCalls=0;private error:unknown;
 constructor(private endpoint:string){}
 /** 同一 operation 内重试时清掉上一次物理尝试的状态。 */
 reset(){this.httpStatus=null;this.calls=0;this.error=undefined}
 private target(input:Parameters<typeof fetch>[0]){const url=input instanceof Request?input.url:String(input);return url.startsWith(this.endpoint)}
 private body(response:Response,maxBytes:number,code:string){if(!response.body)return response;let bytes=0;const body=response.body.pipeThrough(new TransformStream<Uint8Array,Uint8Array>({transform:(chunk,output)=>{bytes+=chunk.length;if(bytes>maxBytes)throw Object.assign(Error(code),{code});output.enqueue(chunk)}}));return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers})}
 fetch(base:typeof fetch,maxBytes:number,code:string):typeof fetch{return async(input,init)=>{if(!this.target(input)){this.auxiliaryCalls++;return base(input,init)}this.calls++;let response:Response;try{response=await base(input,{...init,redirect:'error'})}catch(error){this.error=error;throw error}this.httpStatus=response.status;return this.body(response,maxBytes,code)}}
 /** 失败原因：优先传输错误码，否则 null 由调用方补。 */
 cause(){const error=this.error as {code?:string;message?:string}|undefined;return error?String(error.code??error.message??error):null}
}
