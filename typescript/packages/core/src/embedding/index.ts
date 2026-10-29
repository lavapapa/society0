import {Context,Service} from '@society0/host';import Schema from '@society0/schemastery';import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
import '@society0/host/resources';import '@society0/host/tasks';import '../attempts/index.js';import '../sqlite/index.js';
import type {EmbeddingProvider,EmbeddingLimits,EmbeddingRequest} from '../contracts/cognition.js';import type {Invocation} from '../contracts/common.js';import {EmbeddingQueue} from './broker.js';
export const Config=Schema.object({});export const metadata:PluginMetadata={title:'embedding',summary:'每run精确原文去重、FIFO微批与独立等待者',configVersion:1,configPolicy:[],scope:'run',hostAbi:'society0/host/1'};
export const api:ApiEntry={exportPath:'./embedding',provides:[{key:'embeddings',protocol:'embeddings',major:1,capabilities:[]}],requires:['resources','tasks','attempts','database'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module' as const}))};
declare module '@society0/host'{interface Context{embeddings:Embeddings}}
export class Embeddings extends Service {
 static Config=Config;static inject=['resources','tasks','attempts','database'];private queues=new Map<string,EmbeddingQueue>();private closers=new Map<string,()=>Promise<void>>();
 constructor(ctx:Context){super(ctx,'embeddings')}
 get runId(){return this.ctx.database.runId}
 register(provider:EmbeddingProvider,limits:EmbeddingLimits){const name=provider.identity.provider;if(this.queues.has(name))throw Error('EMBEDDING_PROVIDER_EXISTS');const scope=this.ctx.tasks.bind();const queue=new EmbeddingQueue(this.runId,provider,limits,this.ctx.attempts,async(body,bytes)=>{const task=scope.run('embedding.'+name,body,{bytes});try{return await task}finally{await task.settled}});const close=this.ctx.resources.use('embedding.'+name,async()=>{if(this.queues.get(name)===queue)this.queues.delete(name);await queue.close();this.closers.delete(name)});this.queues.set(name,queue);this.closers.set(name,async()=>{await close()});return close}
 identity(provider:string){const queue=this.queues.get(provider);if(!queue)throw Error('PROVIDER_UNAVAILABLE');return queue.identity}
 embed(request:EmbeddingRequest,context:Invocation){const queue=this.queues.get(request.identity.provider);if(!queue)return Promise.reject(Error('PROVIDER_UNAVAILABLE'));return queue.embed(request,context)}
 diagnostics(){return [...this.queues].map(([provider,queue])=>({provider,...queue.diagnostics()}))}
 async close(){await Promise.all([...this.closers.values()].map(close=>close()))}
}
export default Embeddings;
