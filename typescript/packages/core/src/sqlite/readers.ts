import {SqlRpc} from './rpc.js';
import type {DatabaseOptions} from './contracts.js';
/** 只读连接池：快照与查询分发到固定数量的reader worker，写事务仍由主worker串行。 */
export class ReaderPool {
 readonly workers:SqlRpc[];private next=0;private closing?:Promise<void>;
 constructor(options:DatabaseOptions,count:number){
  this.workers=Array.from({length:count},()=>new SqlRpc({options},options.maxQueuedBytes??32*1024*1024,options.maxPending??128,new URL('./reader.js',import.meta.url)));
 }
 async ready(){await Promise.all(this.workers.map(worker=>worker.call('ready')))}
 pick(){return this.workers[this.next++%this.workers.length]!}
 diagnostics(){return {workers:this.workers.length,requests:this.workers.map(worker=>worker.snapshot().requests)}}
 close(){return this.closing??=(async()=>{await Promise.allSettled(this.workers.map(worker=>worker.close()))})()}
}
