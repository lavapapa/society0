import type {AttemptLedger} from '../attempts/ledger.js';
import {Context,Service} from '@society0/cordis';
import '@society0/host/resources';
import '../sqlite/index.js';import '../sqlite-bootstrap/index.js';import '../execution-fence/index.js';import '../artifacts-local/index.js';
import {History,type HistoryOptions,type CompletePointRequest} from './history.js';
import type {PublicationLease} from '../execution-fence/state.js';
import {Config} from './config.js';
export {Config,metadata,api} from './config.js';export * from './history.js';
declare module '@society0/cordis' {interface Context {history:SqliteHistory}}
export class SqliteHistory extends Service {
 static Config=Config;static inject=['resources','database','executionFence','artifacts','sqliteBootstrap'];private history!:History;private closeOwned!:()=>Promise<void>;
 constructor(ctx:Context,private options:HistoryOptions){super(ctx,'history')}
 async [Service.init](){const resource=await this.ctx.resources.open(async()=>{const value=await History.open(this.options,{database:this.ctx.database,fence:this.ctx.executionFence,artifacts:this.ctx.artifacts});return {value,close:()=>value.close()}},'history.files');this.history=resource.value;this.closeOwned=resource.close;this.ctx.sqliteBootstrap.bindPublisher({publishRoot:(request,seed)=>resource.value.publishRoot(request,seed),probe:(directory,operationId)=>resource.value.probeRoot(directory,operationId)})}
 bindAttempts(attempts:Pick<AttemptLedger,'authority'|'verifyAuthority'|'snapshot'>){return this.ctx.effect(()=>this.history.bindAttempts(attempts))}
 bindIOObserver(callback:(phase:string,operationId:string)=>Promise<void>|void){return this.ctx.effect(()=>this.history.bindIOObserver(callback))}
 bindQuiescence(callback:()=>Promise<void>){return this.ctx.effect(()=>this.history.bindQuiescence(callback))}
 describe(pointId:string){return this.history.describe(pointId)}
 current(){return this.history.current()}
 publish(lease:PublicationLease,request:CompletePointRequest){return this.history.publish(lease,request)}
 probe(operationId:string){return this.history.probe(operationId)}
 probeFork(request:Parameters<History['probeFork']>[0]){return this.history.probeFork(request)}
 fork(request:Parameters<History['fork']>[0]){return this.history.fork(request)}
 export(pointId:string){return this.history.export(pointId)}
 compact(pointId:string){return this.history.compact(pointId)}
 status(){return this.history.status()}
 activate(){return this.history.activate()}
 diagnostics(){return this.history.diagnostics()}
 close(){return this.closeOwned()}
}
export default SqliteHistory;
