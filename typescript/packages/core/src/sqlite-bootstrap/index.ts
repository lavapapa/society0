import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({schemaFailure:Schema.const('abort-create').default('abort-create'),maxTransactionMs:Schema.natural().min(1).default(15000),maxInputBytes:Schema.natural().min(1).default(1048576),maxCapturedBytes:Schema.natural().min(1).default(1048576)});
export const metadata:PluginMetadata={"title":"sqlite-bootstrap","summary":"Private schema and seed preparation with explicit durable root publisher","configVersion":1,"configPolicy":[{"path":"/maxTransactionMs","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxInputBytes","mode":"new-run","affectsResults":true,"secret":false},{"path":"/maxCapturedBytes","mode":"new-run","affectsResults":true,"secret":false},{"path":"/schemaFailure","mode":"new-run","affectsResults":true,"secret":false}],"scope":"run","allowedScopes":["run","bootstrap"],"hostAbi":"society0/host/1"};
export const api:ApiEntry={"exportPath":"./sqlite-bootstrap","provides":[{"key":"sqliteBootstrap","protocol":"sqliteBootstrap","major":1,"capabilities":[]}],"requires":[{"service":{"key":"database","protocol":"database","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"initializers","protocol":"initializers","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"artifacts","protocol":"artifacts","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"executionFence","protocol":"executionFence","major":1,"capabilities":[]},"optional":false,"phase":"module"}]};
import {Context,Service} from '@society0/cordis';
import {resolve} from 'node:path';
import '../sqlite/index.js';
import '../initializers/index.js';
import '../execution-fence/index.js';
import {fault,positive,type SqliteMigration,type RuntimeFrontier,type SqliteSchemaVersion} from '../sqlite/contracts.js';
import type {SeedManifest,BootstrapContext} from '../initializers/index.js';
export interface CompletePoint {readonly id:string;readonly runId:string;readonly kind:'root'|'advance';readonly sequence:string;readonly revision:string;readonly generation:string;readonly controlSequence:string;readonly schema:readonly SqliteSchemaVersion[];readonly frontier:RuntimeFrontier}
export interface SqliteCreateRequest {readonly operationId:string;readonly runId:string;readonly directory:string;readonly input:unknown;readonly migrations:readonly SqliteMigration[];readonly initialFrontier:RuntimeFrontier;readonly initialControl:{generation:string;controlSequence:string;planId:string};readonly requiredInitializerIds:readonly string[]}
export type RootCreationProbe={status:'created';root:CompletePoint}|{status:'prepared';seed:SeedManifest}|{status:'not-started'}|{status:'unknown';reason:string};
/** PR05 owns durable files, object pins, publication lease and descriptor validation. */
export interface RootPublisher {publishRoot(request:SqliteCreateRequest,seed:SeedManifest):Promise<CompletePoint>;probe(directory:string,operationId:string):Promise<RootCreationProbe>}
import {saveBootstrapJournal as save,readBootstrapJournal as journal} from './journal.js';
export {recordForkPrepared} from './journal.js';
declare module '@society0/cordis' {interface Context {sqliteBootstrap:SqliteBootstrap}}
export class SqliteBootstrap extends Service {
 static Config=Config;
 static inject=['database','initializers','artifacts','executionFence'];private publisher?:RootPublisher;private creating=false;
 constructor(ctx:Context,private config:{schemaFailure?:'abort-create';maxTransactionMs?:number;maxInputBytes?:number;maxCapturedBytes?:number}={}){super(ctx,'sqliteBootstrap');if(config.schemaFailure!==undefined&&config.schemaFailure!=='abort-create')throw fault('BOOTSTRAP_POLICY');for(const name of ['maxTransactionMs','maxInputBytes','maxCapturedBytes'] as const)if(config[name]!==undefined)positive(config[name]!,name)}
 bindPublisher(publisher:RootPublisher){if(this.publisher)throw fault('ROOT_PUBLISHER_BOUND');return this.ctx.effect(()=>{this.publisher=publisher;return ()=>{if(this.publisher===publisher)this.publisher=undefined}})}
 async probe(directory:string,operationId:string):Promise<RootCreationProbe>{
  try{if(this.publisher){const published=await this.publisher.probe(directory,operationId);if(published.status==='created'||published.status==='unknown')return published}
   const state=await journal(directory);if(!state)return {status:'not-started'};if(state.operationId!==operationId)return {status:'unknown',reason:'creation operation differs'};return state.status==='prepared'&&state.seed?{status:'prepared',seed:state.seed}:{status:'unknown',reason:'initial preparation incomplete'};
  }catch(error){return {status:'unknown',reason:String(error)}}
 }
  private async completeFrontier(request:SqliteCreateRequest,seed:SeedManifest,lease:import('../execution-fence/state.js').WriteLease):Promise<SeedManifest>{
    const operationId=request.operationId+'/frontier';
    await this.ctx.database.transaction(lease,{operationId,maxDurationMs:this.config.maxTransactionMs??15000,maxStatements:seed.operationIds.length+1,maxInputBytes:this.config.maxInputBytes??1048576,maxChangedRows:16,maxCapturedBytes:this.config.maxCapturedBytes??1048576},async tx=>this.ctx.database.metadata.complete(tx,request.initialFrontier,seed.operationIds));
    const manifest={...seed,operationIds:[...seed.operationIds,operationId]};
     return manifest;
  }
 async prepare(request:SqliteCreateRequest):Promise<SeedManifest>{
  if(this.creating)throw fault('BOOTSTRAP_BUSY');if(request.runId!==this.ctx.database.runId||resolve(request.directory)!==resolve(this.ctx.database.directory))throw fault('SQLITE_RUN_ID');
  const result=await this.probe(request.directory,request.operationId);
  if(result.status==='prepared')return result.seed;
  if(result.status==='created')throw fault('ROOT_ALREADY_CREATED');
  if(result.status==='unknown')throw fault('BOOTSTRAP_UNKNOWN',result.reason);
  this.creating=true;
  try{
   await save(request.directory,{operationId:request.operationId,runId:request.runId,status:'preparing'},true);
   const schema=await this.ctx.database.applyMigrations(request.migrations);
   const reservation=await this.ctx.executionFence.reserve({id:request.operationId});
   try{
    const context:BootstrapContext={ctx:this.ctx,runId:request.runId,generation:request.initialControl.generation,schema,initialTime:request.initialFrontier.logicalTime,input:request.input,signal:reservation.signal,database:this.ctx.database,writeLease:reservation.lease,artifacts:this.ctx.artifacts};
    const seed=await this.ctx.initializers.prepare(context,request.requiredInitializerIds);
     const manifest=await this.completeFrontier(request,seed,reservation.lease);
    await save(resolve(request.directory),{operationId:request.operationId,runId:request.runId,status:'prepared',seed:manifest});return manifest;
   }finally{await reservation.release()}
  }finally{this.creating=false}
 }
 async create(request:SqliteCreateRequest):Promise<CompletePoint>{
  if(!this.publisher)throw fault('ROOT_PUBLISHER_REQUIRED','install PR05 durable root publisher before exposing a run');
  const prior=await this.probe(request.directory,request.operationId);if(prior.status==='created')return prior.root;
  const seed=prior.status==='prepared'?prior.seed:await this.prepare(request);return this.publisher.publishRoot(request,seed);
 }
}
export default SqliteBootstrap;
