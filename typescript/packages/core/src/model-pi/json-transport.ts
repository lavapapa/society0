import {Buffer} from 'node:buffer';

export type ModelTransport='sse'|'json';
/** 未配置的模型默认走非流式；sse 只作为按模型的可选项。 */
export function resolveTransport(configured:Readonly<Record<string,ModelTransport>>|undefined,modelId:string):ModelTransport{return configured?.[modelId]==='sse'?'sse':'json'}

interface JsonTransportOptions{timeoutMs:number;onWireBody:(body:unknown)=>Promise<void>;onResponse:(text:string)=>Promise<void>}
const jsonHeaders=(headers:HeadersInit|undefined)=>{const value=new Headers(headers);value.delete('content-length');return value};

function chunk(payload:Record<string,unknown>){return 'data: '+JSON.stringify(payload)+'\n\n'}
function envelope(response:Response,body:any,extra:Record<string,unknown>){return {id:body?.id??response.headers.get('x-request-id')??'json-transport',object:'chat.completion.chunk',created:body?.created??0,model:body?.model??'',synthetic:true,...extra}}
function failed(response:Response,code:string){return new Response('data: '+JSON.stringify({error:{message:code,code},synthetic:true})+'\n\n',{status:200,headers:{'content-type':'text/event-stream'}})}
function contentText(value:unknown):string|undefined{if(typeof value==='string')return value;if(Array.isArray(value)){const text=value.map(part=>typeof part==='string'?part:typeof (part as {text?:unknown})?.text==='string'?(part as {text:string}).text:'').join('');return text||undefined}return undefined}

/** 把消息形态的 JSON 回包合成为 OpenAI 兼容 SSE，pi-ai 与解析路径保持不变。 */
export function synthesize(response:Response,raw:string):Response{
  let body:any;try{body=JSON.parse(raw)}catch{return failed(response,'MODEL_PI_JSON_INVALID')}
  const choice=Array.isArray(body?.choices)?body.choices[0]:undefined;
  if(!choice||typeof choice!=='object')return failed(response,'MODEL_PI_JSON_CHOICES');
  const message=choice.message??{},events:string[]=[],base=envelope(response,body,{choices:[{index:0,delta:{role:'assistant'},finish_reason:null}]});
  events.push(chunk(base));
  const content=contentText(message.content);
  if(content!==undefined)events.push(chunk(envelope(response,body,{choices:[{index:0,delta:{content},finish_reason:null}]})));
  const reasoning=typeof message.reasoning_content==='string'?message.reasoning_content:typeof message.reasoning==='string'?message.reasoning:undefined;
  if(reasoning!==undefined)events.push(chunk(envelope(response,body,{choices:[{index:0,delta:{reasoning_content:reasoning},finish_reason:null}]})));
  if(Array.isArray(message.tool_calls))for(const [index,toolCall] of message.tool_calls.entries()){const call=toolCall??{},args=typeof call.function?.arguments==='string'?call.function.arguments:JSON.stringify(call.function?.arguments??{});events.push(chunk(envelope(response,body,{choices:[{index:0,delta:{tool_calls:[{index,id:call.id??'call_'+index,type:call.type??'function',function:{name:call.function?.name??'',arguments:args}}]},finish_reason:null}]})))}
  const finish=choice.finish_reason??(Array.isArray(message.tool_calls)&&message.tool_calls.length?'tool_calls':'stop');
  events.push(chunk(envelope(response,body,{choices:[{index:0,delta:{},finish_reason:finish}]})));
  if(body.usage&&typeof body.usage==='object')events.push(chunk(envelope(response,body,{choices:[],usage:body.usage})));
  return new Response(events.join('')+'data: [DONE]\n\n',{status:200,statusText:response.statusText,headers:{'content-type':'text/event-stream'}});
}

export function jsonTransport(inner:typeof fetch,options:JsonTransportOptions):typeof fetch{
  return async(input,init)=>{
    const request=input instanceof Request?input:undefined;
    const url=new URL(request?request.url:String(input));
    if(!url.pathname.endsWith('/chat/completions')||(init?.method??request?.method??'GET').toUpperCase()!=='POST')return inner(input,init);
    const source=init?.body??(request?await request.clone().text():undefined);
    const raw=typeof source==='string'?source:source instanceof Uint8Array?Buffer.from(source).toString():undefined;
    if(raw===undefined)return inner(input,init);
    let body:any;try{body=JSON.parse(raw)}catch{return inner(input,init)}
    body.stream=false;delete body.stream_options;
    await options.onWireBody(body);
    const signal=AbortSignal.any([...(init?.signal?[init.signal]:request?.signal?[request.signal]:[]),AbortSignal.timeout(options.timeoutMs)]);
    const response=await inner(input,{...init,method:'POST',body:JSON.stringify(body),headers:jsonHeaders(init?.headers??request?.headers),signal});
    if(!response.ok)return response;
    const text=await response.text();await options.onResponse(text);return synthesize(response,text);
  };
}
