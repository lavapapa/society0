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
import {Exploration} from '../.build/core/dist/exploration/index.js';
import Public,{publicMigration} from '../.build/core/dist/public/index.js';
import MomentViews,{momentViewsMigration} from '../.build/core/dist/moment-views/index.js';

export const table={path:'/public/market/prices',title:'价格',description:'日度价格',schema:{type:'object',properties:{id:{type:'string'},at:{type:'string'},price:{type:'string'},note:{type:'string'}}},key:['id'],sort:[{field:'at'},{field:'id'}],shard:{by:'at',format:'month'},decimals:['price']};
export const single={path:'/public/actors',title:'主体',description:'公开字段',schema:{type:'object',properties:{id:{type:'string'},role:{type:'string'},name:{type:'string'}}},key:['id'],sort:[{field:'name'}],shard:{by:'',format:'none'}};
export const request=operationId=>({operationId,maxDurationMs:20000,maxStatements:200000,maxInputBytes:16777216,maxChangedRows:100000,maxCapturedBytes:33554432});
export const page={maxItems:20000,maxResponseBytes:67108864};
export const frontier=()=>({codec:'step/1',value:0,logicalTime:0,completedCount:'0',pendingAcknowledgements:[]});
export const migrationList=(source={consumer:'b4-mount'})=>[artifactsMigration,publicMigration(source),momentViewsMigration(source)];

export async function setup({directory,tables=[table,single],docs=[],momentViews='clock',readerWorkers=2,maxSnapshots=8,audiences,migrate=true,resume}={}){
 const host=await createHost({boot:[]});
 try{
  const base=directory?undefined:await mkdtemp(join(tmpdir(),'b4-mount-')),run=directory??join(base,'run');
  await host.ctx.plugin(OwnedResources);
  await host.ctx.plugin(TaskSupervisor,{concurrency:8,maxBytes:134217728});
  await host.ctx.plugin(CompositionStrategies);
  await host.ctx.plugin(strategies);
  await host.ctx.plugin(Slots);
  await host.ctx.plugin(ExecutionFence);
  await host.ctx.plugin(SqliteDatabase,{directory:run,runId:'b4',generation:'g',maxTransactionMs:20000,maxSnapshots,readerWorkers,maxInputBytes:16777216,maxQueuedBytes:67108864,maxQueryBytes:16777216,maxQueryRows:100000,maxInlineBytes:16777216});
  await host.ctx.plugin(ArtifactsLocal,{directory:join(run,'objects')});
  if(resume){
   const publication=await host.ctx.executionFence.seal();
   await host.ctx.database.activateFromCompletePoint(publication,resume);
   host.ctx.executionFence.resume(publication);
  }else if(migrate)await host.ctx.database.applyMigrations(migrationList());
  await host.ctx.plugin(Exploration,{maxInlineFileBytes:16777216,maxSearchBytes:67108864,maxSearchOutputBytes:16777216,maxSearchMs:30000});
  await host.ctx.plugin(Public,{});
  await host.ctx.plugin(MomentViews,{freeze:momentViews});
  for(const decl of tables)host.ctx.public.registry.table(decl);
  for(const decl of docs)host.ctx.public.registry.doc(decl);
  if(audiences)host.ctx.public.registry.audience(audiences);
  let ctx;const inject=['public','database','executionFence','exploration','momentViews','artifacts'];
  await host.ctx.plugin({inject,apply(c){ctx=c}});
  const reservation=await ctx.executionFence.reserve({id:'b4'});
  let released=false;
  const scope=(actorId='a',momentId='m')=>({actorId,momentId,query:null});
  const acquisition=(actorId='a',momentId='m',operationId='b4-op')=>({runId:'b4',generation:'g',operationId,signal:reservation.signal,writeLease:reservation.lease,database:ctx.database,actorId,momentId});
  const openMoment=async(momentId='m',operationId='b4-open')=>runTx(`moment-${operationId}`,tx=>ctx.momentViews.open(tx,momentId));
  const runTx=(operationId,body)=>ctx.database.transaction(reservation.lease,request(operationId),tx=>body(tx));
  const read=async body=>{const view=await ctx.database.snapshot({maxAgeMs:30000});try{return await body(view)}finally{await view.close()}};
  const close=async()=>{if(!released){released=true;await reservation.release()}await host.close()};
  const seal=async()=>{
   if(released)throw Error('B4_ALREADY_RELEASED');
   const point=await read(async view=>({revision:view.revision,frontier:await ctx.database.metadata.read(view),schema:view.schema}));
   released=true;await reservation.release();
   const lease=await ctx.executionFence.seal();
   return {id:'b4-point',runId:'b4',revision:point.revision,frontier:point.frontier,schema:point.schema,lease};
  };
  return {host,ctx,directory:run,reservation,scope,acquisition,openMoment,tx:runTx,read,seal,close};
 }catch(error){await host.close();throw error}
}
