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

export const table={path:'/public/market/prices',title:'价格',description:'日度价格',schema:{type:'object',properties:{id:{type:'string'},at:{type:'string'},price:{type:'string'},note:{type:'string'},meta:{type:'object'},count:{type:'integer'}}},key:['id'],sort:[{field:'at'},{field:'id'}],shard:{by:'at',format:'month'},decimals:['price']};
export const single={path:'/public/actors',title:'主体',description:'公开字段',schema:{type:'object',properties:{id:{type:'string'},role:{type:'string'},name:{type:'string'}}},key:['id'],sort:[{field:'name'}],shard:{by:'',format:'none'}};
export const request=operationId=>({operationId,maxDurationMs:20000,maxStatements:200000,maxInputBytes:16777216,maxChangedRows:100000,maxCapturedBytes:33554432});

export async function setup({tables=[table,single],docs=[],extra=[]}={}){
 const host=await createHost({boot:[]});
 try{
  const base=await mkdtemp(join(tmpdir(),'b2-store-')),directory=join(base,'run');
  await host.ctx.plugin(OwnedResources);
  await host.ctx.plugin(TaskSupervisor,{concurrency:8,maxBytes:134217728});
  await host.ctx.plugin(CompositionStrategies);
  await host.ctx.plugin(strategies);
  await host.ctx.plugin(Slots);
  await host.ctx.plugin(ExecutionFence);
  await host.ctx.plugin(SqliteDatabase,{directory,runId:'b2',generation:'g',maxTransactionMs:20000,maxSnapshots:32,maxInputBytes:16777216,maxQueuedBytes:67108864,maxQueryBytes:16777216,maxQueryRows:100000,maxInlineBytes:16777216});
  await host.ctx.plugin(ArtifactsLocal,{directory:join(directory,'objects')});
  const source={consumer:'b2-store'};
  await host.ctx.database.applyMigrations([artifactsMigration,publicMigration(source),...extra]);
  await host.ctx.plugin(Public,{});
  for(const decl of tables)host.ctx.public.registry.table(decl);
  for(const decl of docs)host.ctx.public.registry.doc(decl);
  let ctx;const inject=['public','database','executionFence'];
  await host.ctx.plugin({inject,apply(c){ctx=c}});
  const reservation=await ctx.executionFence.reserve({id:'b2'});
  return {host,ctx,directory,reservation,close:async()=>{await reservation.release();await host.close()},
   tx:(operationId,body)=>ctx.database.transaction(reservation.lease,request(operationId),tx=>body(tx)),
   read:async body=>{const view=await ctx.database.snapshot({maxAgeMs:30000});try{return await body(view)}finally{await view.close()}}};
 }catch(error){await host.close();throw error}
}
