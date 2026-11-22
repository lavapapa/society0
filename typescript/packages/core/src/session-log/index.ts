import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import {join} from 'node:path';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
import '@society0/host/resources';import '../sqlite-history/index.js';
import {SessionLog,type SessionKey,type SessionHeads,type RequestInput,type OperationAttempt} from './store.js';
export * from './store.js';export {TransportObserver} from './transport.js';
export const Config=Schema.object({directory:Schema.string().required(),flushMs:Schema.natural().min(1).default(200)});
export const metadata:PluginMetadata={title:'session-log',summary:'每个线程一个只追加 JSONL：模型请求增量上下文与外部调用结果',configVersion:1,scope:'run',allowedScopes:['run','bootstrap'],hostAbi:'society0/host/1',configPolicy:['directory','flushMs'].map(key=>({path:'/'+key,mode:'new-run' as const,affectsResults:false}))};
export const api:ApiEntry={exportPath:'./session-log',provides:[{key:'sessions',protocol:'sessions',major:1,capabilities:[]}],requires:['resources','history'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module' as const}))};
declare module '@society0/host' {interface Context {sessions:Sessions}}
/** 运行目录（directory）下的 sessions/；发布点与恢复通过 history.bindSessions 取 heads/truncate。 */
export class Sessions extends Service {
 static Config=Config;static inject=['resources','history'];readonly log:SessionLog;
 constructor(ctx:Context,config:ReturnType<typeof Config>){super(ctx,'sessions');this.log=new SessionLog(join(config.directory,'sessions'),config.flushMs)}
 [Service.init](){this.ctx.resources.use('sessions',()=>this.log.close());this.ctx.history.bindSessions({heads:()=>this.log.heads(),truncate:heads=>this.log.truncate(heads)})}
 append(key:SessionKey,type:string,data:unknown){this.log.append(key,type,data)}
 request(key:SessionKey,input:RequestInput,data:Record<string,unknown>){this.log.request(key,input,data)}
 barrier(){return this.log.barrier()}
 heads():Promise<SessionHeads>{return this.log.heads()}
 truncate(heads:SessionHeads){return this.log.truncate(heads)}
 read(key:SessionKey){return this.log.read(key)}
 find(key:SessionKey,address:OperationAttempt){return this.log.find(key,address)}
}
export default Sessions;
