import {Worker} from 'node:worker_threads';
import type {ExplorationFiles,FileLimits} from './filesystem.js';
export interface ShellOptions extends FileLimits{maxCommandBytes:number;maxPendingRpc:number;timeoutMs:number;maxWorkerMb:number}
interface ShellResult{exitCode:number;stdout:string;stderr:string}
export class ShellInvocation {
 private worker:Worker;private exit=Promise.withResolvers<void>();private accepted=new Set<Promise<void>>();private result?:ShellResult;private failure:unknown;private accepting=true;
 constructor(command:string,private files:ExplorationFiles,private config:ShellOptions,private signal:AbortSignal){this.worker=new Worker(new URL('./worker.js',import.meta.url),{workerData:{...config,command},env:{},execArgv:[],resourceLimits:{maxOldGenerationSizeMb:config.maxWorkerMb}})}
 private stop=(reason:unknown)=>{this.failure??=reason;this.accepting=false;void this.worker.terminate()};
 private abort=()=>this.stop(this.signal.reason);
 private send(message:unknown){if(this.accepting)this.worker.postMessage(message)}
 private rpcFailure(id:unknown,error:unknown){const code=(error as {code?:string})?.code;if(['SQLITE_COMMIT_UNKNOWN','SQLITE_WORKER_LOST'].includes(code??'')){this.stop(error);return}this.send({id,error:{message:String(error),code}})}
 private admit(message:any){if(!this.accepting)return false;if(this.accepted.size>=this.config.maxPendingRpc||Buffer.byteLength(JSON.stringify(message))>this.config.maxRpcBytes){this.stop(Error('SHELL_RPC_BUDGET'));return false}return true}
 private rpc(message:any){if(!this.admit(message))return;const work=this.files.dispatch(message.op,message.args).then(value=>this.send({id:message.id,value}),error=>this.rpcFailure(message.id,error));this.accepted.add(work);void work.then(()=>this.accepted.delete(work),error=>{this.accepted.delete(work);this.stop(error)})}
 private receive=(message:any)=>{if(message.kind==='done'){this.result=message.result;return}if(message.kind==='failure'){this.stop(Object.assign(Error(message.error.message),{code:message.error.code}));return}this.rpc(message)};
 private output(){if(this.failure)throw this.failure;if(!this.result)throw Error('SHELL_WORKER_EXIT');if(Buffer.byteLength(this.result.stdout)+Buffer.byteLength(this.result.stderr)>this.config.maxOutputBytes)throw Error('SHELL_OUTPUT_BYTES');return this.result}
 async run(){const timer=setTimeout(()=>this.stop(Error('SHELL_TIMEOUT')),this.config.timeoutMs);this.worker.once('error',this.stop);this.worker.once('exit',()=>{this.accepting=false;this.exit.resolve()});this.worker.on('message',this.receive);this.signal.addEventListener('abort',this.abort,{once:true});if(this.signal.aborted)this.abort();try{await this.exit.promise;await Promise.allSettled([...this.accepted]);return this.output()}finally{clearTimeout(timer);this.signal.removeEventListener('abort',this.abort)}}
}
