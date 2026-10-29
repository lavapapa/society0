import {Context,Service} from '@society0/cordis';
import Schema from '@society0/schemastery';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {once} from 'node:events';
import type {PluginMetadata,ApiEntry} from '../module-resolver/index.js';
import type {ObservationPort,ObservationQuery,ObservationStream} from './ports.js';
import '../run-directory/index.js';import '../inspector/index.js';
export * from './ports.js';
export const Config=Schema.object({maxPageSize:Schema.natural().min(1).max(10000).default(100),maxRequestBytes:Schema.natural().min(256).default(65536)});
export const metadata:PluginMetadata={title:'observation-gateway',summary:'Borrow native run scopes for read-only observation',configVersion:1,scope:'host',allowedScopes:['host'],hostAbi:'society0/host/1',configPolicy:[{path:'/maxPageSize',mode:'live',affectsResults:false},{path:'/maxRequestBytes',mode:'live',affectsResults:false}]};
export const api:ApiEntry={exportPath:'./observation-gateway',provides:[{key:'observationGateway',protocol:'observationGateway',major:1,capabilities:[]}],requires:['runDirectory','inspector'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module'}))};
declare module '@society0/cordis' {interface Context {observationGateway:ObservationGateway}}
export class ObservationGateway extends Service {
 static Config=Config;static inject=['runDirectory','inspector'];private config:ReturnType<typeof Config>;
 constructor(ctx:Context,input={}){super(ctx,'observationGateway');this.config=Config(input)}
 private async borrow(runId:string){
  const lease=this.ctx.runDirectory.acquire(runId,'read');let consumer:Awaited<ReturnType<Context['plugin']>>|undefined;let observation:ObservationPort|undefined;
  try{consumer=await lease.ctx.plugin({name:'observation.borrow',inject:['observation'],apply(ctx){observation=Reflect.get(ctx,'observation') as ObservationPort}});if(!observation)throw Error('OBSERVATION_UNAVAILABLE '+runId);return {observation,release:async()=>{try{await consumer!.dispose()}finally{await lease.release()}}}}
  catch(error){try{await consumer?.dispose()}finally{await lease.release()}throw error}
 }
 async status(runId:string){const {release,observation}=await this.borrow(runId);try{return await observation.status()}finally{await release()}}
 async query(runId:string,request:ObservationQuery,signal?:AbortSignal){const size=request.pageSize??this.config.maxPageSize;if(!Number.isSafeInteger(size)||size<1||size>this.config.maxPageSize)throw Error('GATEWAY_PAGE_SIZE');signal?.throwIfAborted();const {release,observation}=await this.borrow(runId);try{return await observation.query({...request,pageSize:size},signal)}finally{await release()}}
 async closeCursor(runId:string,cursor:string){const {release,observation}=await this.borrow(runId);try{await observation.closeCursor(cursor)}finally{await release()}}
 async subscribe(runId:string,options:{signal?:AbortSignal}={}):Promise<ObservationStream>{
  const {release,observation}=await this.borrow(runId);let source:ObservationStream;try{source=observation.subscribe(options)}catch(error){await release();throw error}
  return ownedStream(source,release,options.signal);
 }

 private async readQuery(request:IncomingMessage):Promise<ObservationQuery>{
  const chunks:Buffer[]=[];let bytes=0;
  for await(const chunk of request){
   const data=Buffer.from(chunk);bytes+=data.length;
   if(bytes>this.config.maxRequestBytes)throw Error('GATEWAY_REQUEST_BYTES');
   chunks.push(data);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8')) as ObservationQuery;
 }
 private async display(runId:string,response:ServerResponse,signal:AbortSignal){
  const stream=await this.subscribe(runId,{signal});
  try{
   response.writeHead(200,{'content-type':'application/x-ndjson'});
   for await(const event of stream){
    signal.throwIfAborted();
    if(!response.write(JSON.stringify(event)+'\n'))await once(response,'drain',{signal});
   }
   response.end();
  }finally{await stream.close()}
 }
 private async route(request:IncomingMessage,response:ServerResponse,signal:AbortSignal){
  const url=new URL(request.url??'/', 'http://localhost');
  const match=/^\/runs\/([^/]+)\/(status|query|subscribe)$/.exec(url.pathname);
  if(!match){response.writeHead(404);response.end();return}
  const runId=decodeURIComponent(match[1]!);const operation=match[2];
  if(operation==='status'&&request.method==='GET'){
   sendJson(response,await this.status(runId),signal);return;
  }
  if(operation==='query'&&request.method==='POST'){
   sendJson(response,await this.query(runId,await this.readQuery(request),signal),signal);return;
  }
  if(operation==='subscribe'&&request.method==='GET'){
   await this.display(runId,response,signal);return;
  }
  response.writeHead(405);response.end();
 }
 /** Attach to an explicitly owned HTTP server. This entry never opens a listener. */
 async handle(request:IncomingMessage,response:ServerResponse):Promise<void>{
  const cancel=new AbortController();const abort=()=>cancel.abort(Error('OBSERVATION_HTTP_DISCONNECTED'));
  request.once('aborted',abort);response.once('close',abort);
  try{await this.route(request,response,cancel.signal)}
  catch(error){sendError(response,error,cancel.signal)}
  finally{request.removeListener('aborted',abort);response.removeListener('close',abort)}
 }
}
function ownedStream(source:ObservationStream,release:()=>Promise<void>,signal?:AbortSignal):ObservationStream{
 let closed=false;
 const close=async()=>{
  if(closed)return;closed=true;signal?.removeEventListener('abort',abort);
  try{await source.close()}finally{await release()}
 };
 const abort=()=>{void close()};signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)void close();
 return {[Symbol.asyncIterator](){return this},close,return:async()=>{await close();return {done:true,value:undefined}},next:()=>nextOwned(source,close)};
}
async function nextOwned(source:ObservationStream,close:()=>Promise<void>){
 try{const item=await source.next();if(item.done)await close();return item}
 catch(error){await close();throw error}
}
function sendJson(response:ServerResponse,result:unknown,signal:AbortSignal){
 signal.throwIfAborted();response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify(result));
}
function sendError(response:ServerResponse,error:unknown,signal:AbortSignal){
 if(signal.aborted||response.destroyed)return;
 if(!response.headersSent)response.writeHead(400,{'content-type':'application/json'});
 response.end(JSON.stringify({error:error instanceof Error?error.message:String(error)}));
}
export default ObservationGateway;
