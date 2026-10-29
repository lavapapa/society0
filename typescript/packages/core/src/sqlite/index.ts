import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({directory:Schema.string().required(),runId:Schema.string().required(),generation:Schema.string().required(),busyTimeoutMs:Schema.natural().min(1).default(5000),maxQueuedBytes:Schema.natural().min(1).default(33554432),maxPending:Schema.natural().min(1).default(128),maxTransactionMs:Schema.natural().min(1).default(30000),maxQueryBytes:Schema.natural().min(1).default(1048576),maxQueryRows:Schema.natural().min(1).default(10000),maxSnapshots:Schema.natural().min(1).default(16),maxSnapshotAgeMs:Schema.natural().min(1).default(30000),maxInputBytes:Schema.natural().min(1).default(1048576),maxInlineBytes:Schema.natural().min(1).default(1048576)});
export const metadata:PluginMetadata={"title":"sqlite","summary":"Run-owned native SQLite transactions and snapshots","configVersion":1,"configPolicy":[{"path":"/directory","mode":"new-run","affectsResults":true,"secret":false},{"path":"/runId","mode":"new-run","affectsResults":true,"secret":false},{"path":"/generation","mode":"new-run","affectsResults":true,"secret":false},{"path":"/busyTimeoutMs","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxQueuedBytes","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxPending","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxTransactionMs","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxQueryBytes","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxQueryRows","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxSnapshots","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxSnapshotAgeMs","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxInputBytes","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxInlineBytes","mode":"new-run","affectsResults":true,"secret":false}],"scope":"run","allowedScopes":["run","bootstrap"],"hostAbi":"society0/host/1"};
export const api:ApiEntry={"exportPath":"./sqlite","provides":[{"key":"database","protocol":"database","major":1,"capabilities":[]}],"requires":[{"service":{"key":"resources","protocol":"resources","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"tasks","protocol":"tasks","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"executionFence","protocol":"executionFence","major":1,"capabilities":[]},"optional":false,"phase":"module"}]};
import '../execution-fence/index.js';
import {Context,Service} from '@society0/cordis';
import '@society0/host/resources';
import '@society0/host/tasks';
import {Database} from './database.js';
import type {WriteLease,ExecutionGate} from '../execution-fence/state.js';
import type {DatabaseOptions,TransactionRequest,SqliteTransaction,SqliteRuntimeMetadata,SqliteMigration} from './contracts.js';
export * from './contracts.js';
export {Database} from './database.js';
declare module '@society0/cordis' {interface Context {database:SqliteDatabase}}
/** The closure binds the provider activation before native dependent cleanup begins. */
export class SqliteDatabase extends Service {
 static Config=Config;
 static inject=['resources','tasks','executionFence'];
 private database!:Database;private closeOwned!:()=>Promise<void>;
 constructor(ctx:Context,private options:DatabaseOptions & {generation:string}){super(ctx,'database')}
 async [Service.init](){
  const tasks=this.ctx.tasks.bind();
  const fence=this.ctx.get('executionFence') as Pick<ExecutionGate,'enter'|'halt'|'assertPublication'>;
  const resource=await this.ctx.resources.open(async()=>{const value=await Database.open(this.options,fence,tasks);return {value,close:async()=>{await tasks.drain('sqlite closing');await value.close()}}},'sqlite.worker');
  this.database=resource.value;this.closeOwned=resource.close;
  this.ctx.executionFence.bind({runId:this.options.runId,generation:this.options.generation,readRevision:()=>resource.value.readRevision()});
 }
 get directory(){return this.options.directory}
 get runId(){return this.database.runId}
 get metadata():SqliteRuntimeMetadata{return this.database.metadata}
 readRevision(){return this.database.readRevision()}
 applyMigrations(migrations:readonly SqliteMigration[]){return this.database.applyMigrations(migrations)}
 transaction<T>(lease:WriteLease,request:TransactionRequest,body:(tx:SqliteTransaction)=>Promise<T>){return this.database.transaction(lease,request,body)}
 snapshot(options:{maxAgeMs:number}){return this.database.snapshot(options)}
 probe(operationId:string){return this.database.probe(operationId)}
 activateFromCompletePoint(...args:Parameters<Database['activateFromCompletePoint']>){return this.database.activateFromCompletePoint(...args)}
 nativeDiagnostics(){return this.database.nativeDiagnostics()}
 diagnostics(){return this.database.diagnostics()}
 close(){return this.closeOwned()}
}
export default SqliteDatabase;
