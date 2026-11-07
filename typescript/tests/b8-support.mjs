import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHost,OwnedResources,TaskSupervisor} from '../.build/host/dist/index.js';
import {CompositionStrategies} from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import {Slots} from '../.build/host/dist/slots/index.js';
import {ExecutionFence} from '../.build/core/dist/execution-fence/index.js';
import {SqliteDatabase} from '../.build/core/dist/sqlite/index.js';
import {ArtifactsLocal,artifactsMigration} from '../.build/core/dist/artifacts-local/index.js';
import Public,{publicMigration} from '../.build/core/dist/public/index.js';
import MomentViews,{momentViewsMigration} from '../.build/core/dist/moment-views/index.js';
import {Threads,threadMigration} from '../.build/core/dist/thread/index.js';
import {Perceptions,perceptionMigration} from '../.build/core/dist/perception/index.js';
import {DriverRegistry} from '../.build/core/dist/drivers/index.js';
import {Actors,actorsMigration} from '../.build/core/dist/actors/index.js';
import ActorEligibility from '../.build/core/dist/actor-eligibility/index.js';
import {ActivationPolicy as ActivationMailbox} from '../.build/core/dist/activation-mailbox/index.js';
import {ActivationService,wakePolicySlot} from '../.build/core/dist/activation/index.js';

export const single={path:'/public/actors',title:'主体',description:'公开字段',schema:{type:'object',properties:{id:{type:'string'},role:{type:'string'},name:{type:'string'}}},key:['id'],sort:[{field:'name'}],shard:{by:'',format:'none'}};
export const prices={path:'/public/market/prices',title:'价格',description:'日度价格',schema:{type:'object',properties:{id:{type:'string'},at:{type:'string'},price:{type:'string'}}},key:['id'],sort:[{field:'at'},{field:'id'}],shard:{by:'at',format:'month'}};
export const notices={path:'/public/notices',title:'公告',description:'公告',schema:{type:'object',properties:{id:{type:'string'},text:{type:'string'}}},key:['id'],sort:[{field:'id'}],shard:{by:'',format:'none'}};
export const request=operationId=>({operationId,maxDurationMs:120000,maxStatements:200000,maxInputBytes:16777216,maxChangedRows:100000,maxCapturedBytes:33554432});
export const page={maxItems:20,maxResponseBytes:1<<20};

export async function setup({tables=[single],docs=[],audiences,momentViews='clock',perceptions=false,activation=false,wakePolicy,extra=[]}={}){
 const host=await createHost({boot:[]});
 try{
  const base=await mkdtemp(join(tmpdir(),'b8-changes-')),run=join(base,'run');
  await host.ctx.plugin(OwnedResources);
  await host.ctx.plugin(TaskSupervisor,{concurrency:8,maxBytes:134217728});
  await host.ctx.plugin(CompositionStrategies);
  await host.ctx.plugin(strategies);
  await host.ctx.plugin(Slots);
  await host.ctx.plugin(ExecutionFence);
  await host.ctx.plugin(SqliteDatabase,{directory:run,runId:'b8',generation:'g',maxTransactionMs:120000,busyTimeoutMs:120000,maxSnapshotAgeMs:300000,maxSnapshots:16,readerWorkers:2,maxInputBytes:16777216,maxQueuedBytes:67108864,maxQueryBytes:16777216,maxQueryRows:100000,maxInlineBytes:16777216});
  await host.ctx.plugin(ArtifactsLocal,{directory:join(run,'objects')});
  const migrations=[artifactsMigration,publicMigration({consumer:'b8-changes'}),momentViewsMigration({consumer:'b8-changes'}),...(perceptions?[threadMigration({consumer:'b8-changes'}),perceptionMigration({consumer:'b8-changes'})]:[]),...(activation?[actorsMigration]:[]),...extra];
  await host.ctx.database.applyMigrations(migrations);
  if(perceptions)await host.ctx.plugin(Threads,{});
  if(perceptions)await host.ctx.plugin(Perceptions);
  await host.ctx.plugin(Public,{});
  await host.ctx.plugin(MomentViews,{freeze:momentViews});
  for(const decl of tables)host.ctx.public.registry.table(decl);
  for(const decl of docs)host.ctx.public.registry.doc(decl);
  if(audiences)host.ctx.public.registry.audience(audiences);
  if(activation){
   await host.ctx.plugin(DriverRegistry);
   await host.ctx.plugin(Actors);
   await host.ctx.plugin(ActorEligibility);
   await host.ctx.plugin(ActivationMailbox);
   await host.ctx.plugin(ActivationService,{capacity:4,maxActivations:64});
   await host.ctx.plugin({inject:['slots'],apply(c){c.slots.contribute(wakePolicySlot,{id:'b8-policy',value:wakePolicy})}});
  }
  const names=['public','database','executionFence','momentViews',...(perceptions?['threads','perceptions']:[]),...(activation?['activation']:[])];
  let ctx;await host.ctx.plugin({inject:names,apply(c){ctx=c}});
  const reservation=await ctx.executionFence.reserve({id:'b8'});
  const runTx=(operationId,body)=>ctx.database.transaction(reservation.lease,request(operationId),tx=>body(tx));
  const read=async body=>{const view=await ctx.database.snapshot({maxAgeMs:30000});try{return await body(view)}finally{await view.close()}};
  const openMoment=(momentId='m',operationId='b8-open-'+momentId)=>runTx(`moment-${operationId}`,tx=>ctx.momentViews.open(tx,momentId));
  const revision=async()=>BigInt(await read(view=>view.revision));
  const scope=(actorId='a',momentId='m')=>({actorId,momentId,query:null});
  const context=(operationId,actorId='a',momentId='m')=>({ctx,database:ctx.database,writeLease:reservation.lease,actorId,momentId,logicalTime:1,runId:'b8',generation:'g',operationId,signal:reservation.signal});
  return {host,ctx,directory:run,reservation,runTx,read,openMoment,revision,scope,context,close:async()=>{await reservation.release();await host.close()}};
 }catch(error){await host.close();throw error}
}
