import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHost,OwnedResources,TaskSupervisor,FiberState} from '../.build/host/dist/index.js';
import {RunDirectory} from '../.build/host/dist/run-directory/index.js';
import {ProfileLoader} from '../.build/host/dist/profile-loader/index.js';
import {ModuleResolver} from '../.build/host/dist/module-resolver/index.js';
import {ConfigResolverRegistry} from '../.build/host/dist/config-resolvers/index.js';
import {compileLayers} from '../.build/host/dist/profile-loader/compiler.js';
import {ExecutionFence} from '../.build/core/dist/execution-fence/index.js';
import {SqliteDatabase} from '../.build/core/dist/sqlite/index.js';
import {SqliteBootstrap} from '../.build/core/dist/sqlite-bootstrap/index.js';
test('PR04-T04/T09 failed private preparation never enters directory or generation',async()=>{
 const host=await createHost({boot:[]});await host.ctx.plugin(OwnedResources);await host.ctx.plugin(TaskSupervisor);await host.ctx.plugin(ModuleResolver);await host.ctx.plugin(ConfigResolverRegistry);await host.ctx.plugin(ProfileLoader);await host.ctx.plugin(RunDirectory);
 const directory=await mkdtemp(join(tmpdir(),'pr04-private-'));const generation=await compileLayers([],{},{});
 try{
  await assert.rejects(host.ctx.runDirectory.create({runId:'failed-private',generation,async prepare(ctx){await ctx.plugin(ExecutionFence);await ctx.plugin(SqliteDatabase,{directory,runId:'failed-private',generation:'g',maxTransactionMs:20000});await ctx.plugin({inject:['database'],async apply(consumer){await consumer.database.applyMigrations([{owner:'broken',from:0,to:1,after:[],source:{version:'1'},statements:[{sql:'CREATE TABLE invalid ('}]}])}});throw Error('unreachable publication')}}),/syntax|incomplete/);
  assert.deepEqual(host.ctx.runDirectory.list(),[]);assert.equal(host.ctx.profileLoader.trees().length,0);assert.equal(host.ctx.resources.closeReport().open,1,'directory owns only its host-level shutdown resource');
  assert.throws(()=>host.ctx.runDirectory.acquire('failed-private'),/RUN_UNAVAILABLE/);
  const bootstrap=await host.ctx.plugin(SqliteBootstrap);assert.equal(bootstrap.state,FiberState.PENDING,'production artifacts and database are required before bootstrap activation');
 }finally{await assert.rejects(host.close(),error=>{assert.equal(error.report.drained,true);assert.equal(error.report.resources.open,0);assert.equal(error.report.tasks.activeCount,0);return error.report.status==='halted'})}
});
