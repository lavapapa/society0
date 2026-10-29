import type {ArtifactsLocal} from '../artifacts-local/index.js';
import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({maxInitialBytes:Schema.natural().min(1).default(67108864)});
export const metadata:PluginMetadata={"title":"initializers","summary":"Ordered private run initializers","configVersion":1,"configPolicy":[{"path":"/maxInitialBytes","mode":"new-run","affectsResults":true,"secret":false}],"scope":"run","allowedScopes":["run","bootstrap"],"hostAbi":"society0/host/1"};
export const api:ApiEntry={"exportPath":"./initializers","provides":[{"key":"initializers","protocol":"initializers","major":1,"capabilities":[]}],"requires":[{"service":{"key":"slots","protocol":"slots","major":1,"capabilities":[]},"optional":false,"phase":"module"},{"service":{"key":"tasks","protocol":"tasks","major":1,"capabilities":[]},"optional":false,"phase":"module"}]};
import {Context,Service} from '@society0/cordis';
import {slotKey} from '@society0/host/slots';
import '@society0/host/tasks';
import {orderDependencies} from '../sqlite/schema.js';
import {fault,inputBytes,positive,type SqliteSchemaVersion} from '../sqlite/contracts.js';
import type {Database} from '../sqlite/database.js';
import type {WriteLease} from '../execution-fence/state.js';
export interface BootstrapContext {readonly ctx:Context;readonly runId:string;readonly generation:string;readonly schema:readonly SqliteSchemaVersion[];readonly initialTime:unknown;readonly input:unknown;readonly signal:AbortSignal;readonly database:Pick<Database,'runId'|'metadata'|'transaction'|'snapshot'|'probe'>;readonly writeLease:WriteLease;readonly artifacts:Pick<ArtifactsLocal,'stage'|'read'|'localFile'|'inspect'|'reference'>}
export interface Initializer {readonly id:string;readonly after:readonly string[];initialize(context:BootstrapContext):Promise<void|{operationIds?:readonly string[];artifactIds?:readonly string[]}>}
export interface SeedManifest {readonly initializerIds:readonly string[];readonly operationIds:readonly string[];readonly artifactIds:readonly string[]}
export const initializerSlot=slotKey<Initializer>('society0/initializers');
function validate(value:unknown):Initializer {const i=value as Initializer;if(!i||typeof i.id!=='string'||!Array.isArray(i.after)||typeof i.initialize!=='function')throw fault('INITIALIZER_INVALID');return i}
declare module '@society0/cordis' {interface Context {initializers:Initializers}}
export class Initializers extends Service {
 static Config=Config;
 static inject=['slots','tasks'];private maxInitialBytes:number;
 constructor(ctx:Context,config:{maxInitialBytes?:number}={}){super(ctx,'initializers');this.maxInitialBytes=positive(config.maxInitialBytes??67108864,'maxInitialBytes');ctx.slots.declare({key:initializerSlot,strategy:'ordered',required:false,validate})}
 register(initializer:Initializer){validate(initializer);return this.ctx.slots.contribute(initializerSlot,{id:initializer.id,value:initializer,after:initializer.after})}
 async prepare(context:BootstrapContext,requiredIds:readonly string[]):Promise<SeedManifest>{
  if(inputBytes(context.input)>this.maxInitialBytes)throw fault('INITIALIZER_INPUT_BUDGET');
  const selection=await this.ctx.slots.acquire(initializerSlot,{generation:context.generation,signal:context.signal});
  try{
   const items=selection.items.map(item=>item.value);const ids=new Set(items.map(item=>item.id));for(const id of requiredIds)if(!ids.has(id))throw fault('INITIALIZER_MISSING',id);
   const ordered=orderDependencies(items,item=>item.id,item=>item.after);const operationIds:string[]=[],artifactIds:string[]=[],initializerIds:string[]=[];
   for(const item of ordered){context.signal.throwIfAborted();const task=this.ctx.tasks.run('initializer.'+item.id,async signal=>item.initialize({...context,signal}),{signal:context.signal});let result;try{result=await task}finally{await task.settled}initializerIds.push(item.id);operationIds.push(...result?.operationIds??[]);artifactIds.push(...result?.artifactIds??[])}
   return {initializerIds,operationIds,artifactIds};
  }finally{await selection.release()}
 }
}
export default Initializers;
