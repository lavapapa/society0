import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {mkdir,open,readFile,readdir,rm,stat,truncate,type FileHandle} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {zstdCompressSync,zstdDecompressSync} from 'node:zlib';

/** session = 主体 × 激活线程；世界由运行目录区分，所以文件名不含 runId，父子世界同一线程同名。 */
export interface SessionKey {readonly actorId:string;readonly momentId:string}
/** sessions/ 下文件名 → 字节长度；发布点时写进点记录，恢复时据此截断。 */
export type SessionHeads=Record<string,number>;
export interface SessionEvent {readonly type:string;readonly seq:number;readonly time:string;readonly data:any}
export interface SessionParent {readonly run:string;readonly heads:SessionHeads}
/** 外部调用的身份：同一 operation 的第 attempt 次物理尝试。 */
export interface OperationAttempt {readonly operationId:string;readonly attempt:number}
export interface RequestInput {readonly model:unknown;readonly context:{readonly systemPrompt?:string;readonly messages:readonly unknown[];readonly tools?:readonly unknown[]};readonly options:unknown}
export interface PackedText {readonly codec:'zstd+base64';readonly data:string}

/** 非线程调用（embedding、凭据刷新）落在固定的系统 session 里。 */
export function systemSession(name:string):SessionKey{return {actorId:'@system',momentId:name}}
export function sessionFile(key:SessionKey){return createHash('sha256').update(JSON.stringify([key.actorId,key.momentId])).digest('hex').slice(0,32)+'.jsonl'}
export function packText(text:string):PackedText{return {codec:'zstd+base64',data:zstdCompressSync(Buffer.from(text)).toString('base64')}}
export function unpackText(value:PackedText){return zstdDecompressSync(Buffer.from(value.data,'base64')).toString()}

interface Pending {type:string;time:string;data:any}
interface Session {key:SessionKey;name:string;pending:Pending[];handle?:FileHandle;seq:number;headerSeq?:number;header?:string;context?:string[]}

/** 业务值可能含 bigint（与 action 回执编码一致转字符串）。 */
const json=(value:unknown)=>JSON.stringify(value,(_key,item)=>typeof item==='bigint'?item.toString():item);
const digests=new WeakMap<object,string>();
function digest(value:unknown){const cached=value&&typeof value==='object'?digests.get(value):undefined;if(cached)return cached;const result=createHash('sha1').update(json(value)??'null').digest('base64');if(value&&typeof value==='object')digests.set(value,result);return result}
async function missing(path:string){try{await stat(path);return false}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return true;throw error}}
/** 读 [0,end) 的最后一行（不含结尾换行）。 */
async function lastLine(path:string,end:number){const handle=await open(path,'r');try{const parts:Buffer[]=[];let position=end-1;while(position>0){const size=Math.min(65536,position);position-=size;const buffer=Buffer.alloc(size);await handle.read(buffer,0,size,position);const index=buffer.lastIndexOf(10);if(index>=0){parts.unshift(buffer.subarray(index+1));return Buffer.concat(parts).toString()}parts.unshift(buffer)}return Buffer.concat(parts).toString()}finally{await handle.close()}}

/**
 * 每个 session 一个只追加 JSONL：首行 header，之后 {type,seq,time,data}。
 * 写入在内存攒批，flushMs 或 barrier() 时 write() 交给操作系统，不 fsync；只有 heads() 做 fsync。
 * 分叉的子世界在 sessions/parent.json 记父目录与截断长度；读取时先读父前缀再读自己，写只追加自己的文件。
 */
export class SessionLog {
 readonly directory:string;private sessions=new Map<string,Session>();private dirty=new Set<string>();private created=false;private timer?:ReturnType<typeof setTimeout>;private chain:Promise<void>=Promise.resolve();private failure:unknown;private parents=new Map<string,Promise<{directory:string;heads:SessionHeads}|undefined>>();
 constructor(directory:string,private flushMs=200){this.directory=resolve(directory)}
 private session(key:SessionKey){const name=sessionFile(key);let session=this.sessions.get(name);if(!session){session={key:{actorId:key.actorId,momentId:key.momentId},name,pending:[],seq:0};this.sessions.set(name,session)}return session}
 private push(session:Session,type:string,data:unknown){session.pending.push({type,time:new Date().toISOString(),data});this.timer??=setTimeout(()=>{this.timer=undefined;void this.flush().catch(error=>{this.failure??=error})},this.flushMs);this.timer.unref?.()}
 append(key:SessionKey,type:string,data:unknown){this.push(this.session(key),type,data)}
 /** 写一次模型请求：header 变化才写；上下文是上次的延续就只写新增消息，否则写全量。request 事件的 headerSeq 在落盘时填入。 */
 request(key:SessionKey,input:RequestInput,data:Record<string,unknown>){
  const session=this.session(key),header={model:input.model,options:input.options,systemPrompt:input.context.systemPrompt??null,tools:input.context.tools??[]},headerDigest=digest(header);
  if(headerDigest!==session.header){this.push(session,'request/header',header);session.header=headerDigest}
  const messages=input.context.messages,hashes=messages.map(digest),previous=session.context;
  if(previous&&previous.length<=hashes.length&&previous.every((value,index)=>value===hashes[index])){if(hashes.length>previous.length)this.push(session,'context/append',{messages:messages.slice(previous.length)})}
  else this.push(session,'context/reset',{messages});
  session.context=hashes;this.push(session,'request',{...data,contextLen:hashes.length});
 }
 private parent(directory:string){let value=this.parents.get(directory);if(!value){value=(async()=>{const path=join(directory,'parent.json');if(await missing(path))return undefined;const parent=JSON.parse(await readFile(path,'utf8')) as SessionParent;return {directory:resolve(directory,'..',parent.run,'sessions'),heads:parent.heads}})();this.parents.set(directory,value)}return value}
 /** 链上最后一个事件的 seq；文件只有 header 时继续看父世界。 */
 private async lastSeq(directory:string,name:string,end?:number):Promise<number>{const path=join(directory,name);const size=end??(await missing(path)?0:(await stat(path)).size);if(size>0){const line=JSON.parse(await lastLine(path,size));if(line.type!=='session')return line.seq}const parent=await this.parent(directory);return parent&&parent.heads[name]!==undefined?this.lastSeq(parent.directory,name,parent.heads[name]):-1}
 private async ready(session:Session){if(session.handle)return session.handle;await mkdir(this.directory,{recursive:true});const path=join(this.directory,session.name),fresh=await missing(path);session.seq=await this.lastSeq(this.directory,session.name)+1;const handle=await open(path,'a');if(fresh){await handle.write(JSON.stringify({type:'session',version:1,actorId:session.key.actorId,momentId:session.key.momentId,createdAt:new Date().toISOString()})+'\n');this.created=true}session.handle=handle;return handle}
 private async write(){for(const session of this.sessions.values()){if(!session.pending.length)continue;const handle=await this.ready(session),events=session.pending.splice(0);let text='';for(const event of events){const seq=session.seq++;if(event.type==='request/header')session.headerSeq=seq;const data=event.type==='request'?{...event.data,headerSeq:session.headerSeq}:event.data;text+=json({type:event.type,seq,time:event.time,data})+'\n'}await handle.write(text);this.dirty.add(session.name)}}
 private flush(){clearTimeout(this.timer);this.timer=undefined;const run=this.chain.then(()=>this.write());this.chain=run.catch(()=>{});return run}
 /** 把攒批事件交给操作系统；模型请求发出前调用。 */
 async barrier(){if(this.failure)throw this.failure;await this.flush()}
 /** flush 全部，自上次以来写过的文件各 fsync 一次，返回自己目录下每个文件的长度。 */
 async heads():Promise<SessionHeads>{await this.barrier();const heads:SessionHeads={};for(const name of await this.names()){const session=this.sessions.get(name);if(session?.handle&&this.dirty.has(name))await session.handle.sync();heads[name]=(await stat(join(this.directory,name))).size}if(this.created){const directory=await open(this.directory,'r');try{await directory.sync()}finally{await directory.close()}}this.dirty.clear();this.created=false;return heads}
 /** 截到点上的长度，不在表里的文件删掉；内存里的增量基准一并作废。 */
 async truncate(heads:SessionHeads){await this.barrier();const run=this.chain.then(async()=>{await this.release();for(const name of await this.names()){const path=join(this.directory,name);if(heads[name]===undefined)await rm(path);else await truncate(path,heads[name])}});this.chain=run.catch(()=>{});await run}
 private async names(){if(await missing(this.directory))return [];return (await readdir(this.directory)).filter(name=>name.endsWith('.jsonl')).sort()}
 private async release(){const handles=[...this.sessions.values()].flatMap(session=>session.handle?[session.handle]:[]);this.sessions.clear();this.dirty.clear();await Promise.all(handles.map(handle=>handle.close()))}
 private async* lines(directory:string,name:string,end?:number):AsyncGenerator<SessionEvent>{const parent=await this.parent(directory);if(parent&&parent.heads[name]!==undefined)yield* this.lines(parent.directory,name,parent.heads[name]);const path=join(directory,name);if(end===0||await missing(path))return;const input=createReadStream(path,{start:0,...(end===undefined?{}:{end:end-1})});for await(const line of createInterface({input,crlfDelay:Infinity})){if(!line)continue;const event=JSON.parse(line);if(event.type!=='session')yield event}}
 /** 父世界前缀（按 parent.json 截断，可递归）加自己的事件；不含 header。 */
 async* read(key:SessionKey):AsyncGenerator<SessionEvent>{await this.barrier();yield* this.lines(this.directory,sessionFile(key))}
 /** 某次尝试的最后一个结论事件（request 事件不算）。 */
 async find(key:SessionKey,address:OperationAttempt){let found:SessionEvent|undefined;for await(const event of this.read(key))if(event.type!=='request'&&event.data?.operationId===address.operationId&&event.data?.attempt===address.attempt)found=event;return found}
 async close(){await this.flush();await this.release()}
}
