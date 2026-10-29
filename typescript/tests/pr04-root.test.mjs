import {test} from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,stat} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createHost,OwnedResources,TaskSupervisor} from '../.build/host/dist/index.js';
import {CompositionStrategies} from '../.build/host/dist/composition-strategies/index.js';import slotStrategies from '../.build/host/dist/slot-strategies/index.js';import {Slots} from '../.build/host/dist/slots/index.js';
import {ExecutionFence} from '../.build/core/dist/execution-fence/index.js';import {SqliteDatabase} from '../.build/core/dist/sqlite/index.js';
import {Initializers} from '../.build/core/dist/initializers/index.js';import {SqliteBootstrap} from '../.build/core/dist/sqlite-bootstrap/index.js';
import {ArtifactsLocal,artifactsMigration} from '../.build/core/dist/artifacts-local/index.js';import {SqliteHistory} from '../.build/core/dist/sqlite-history/index.js';
const txRequest=operationId=>({operationId,maxDurationMs:15000,maxStatements:20,maxInputBytes:65536,maxChangedRows:100,maxCapturedBytes:1048576});
import {RunDirectory} from '../.build/host/dist/run-directory/index.js';import {ProfileLoader} from '../.build/host/dist/profile-loader/index.js';import {ModuleResolver} from '../.build/host/dist/module-resolver/index.js';import {ConfigResolverRegistry} from '../.build/host/dist/config-resolvers/index.js';import {compileLayers} from '../.build/host/dist/profile-loader/compiler.js';
test('PR04-T03/T09 run-directory publishes real roots, isolates runs and reopens without reseeding',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'pr04-root-')),other=await mkdtemp(join(tmpdir(),'pr04-other-'));const seeds=new Map(),preparationInputs=[];
 const host=await createHost({boot:[]});await host.ctx.plugin(OwnedResources);await host.ctx.plugin(TaskSupervisor);await host.ctx.plugin(CompositionStrategies);await host.ctx.plugin(slotStrategies);await host.ctx.plugin(ModuleResolver);await host.ctx.plugin(ConfigResolverRegistry);await host.ctx.plugin(ProfileLoader);await host.ctx.plugin(RunDirectory);
 const generation=await compileLayers([],{},{});
 const plan=(runId,directory,name)=>({runId,generation,async prepare(ctx){
  await ctx.plugin(Slots);await ctx.plugin(ExecutionFence);await ctx.plugin(SqliteDatabase,{directory,runId,generation:'g',maxTransactionMs:20000});await ctx.plugin(ArtifactsLocal,{directory:join(directory,'artifacts')});await ctx.plugin(Initializers);await ctx.plugin(SqliteBootstrap,{maxTransactionMs:15000,maxInputBytes:65536});await ctx.plugin(SqliteHistory,{directory,runId,generation:'g',controlSequence:'0'});
  await ctx.plugin({inject:['initializers'],apply(owner){owner.initializers.register({id:'actors',after:[],async initialize(context){seeds.set(runId,(seeds.get(runId)??0)+1);await context.database.transaction(context.writeLease,txRequest('seed-actors'),tx=>tx.execute({sql:'INSERT INTO actors VALUES (1,?)',parameters:[context.input.name]}));return {operationIds:['seed-actors']}}})}});
  let prepared;await ctx.plugin({inject:['database','sqliteBootstrap','history'],async apply(owner){
   const request={operationId:'root-creation',runId,directory,input:{name,rows:Array.from({length:8192},(_,id)=>({id,description:'x'.repeat(128)}))},migrations:[artifactsMigration,{owner:'actors',from:0,to:1,after:['artifacts-local'],source:{version:'1'},statements:[{sql:'CREATE TABLE actors(id INTEGER PRIMARY KEY,name TEXT NOT NULL)'}]}],initialFrontier:{codec:'step/1',value:0,logicalTime:0,completedCount:'0',pendingAcknowledgements:[]},initialControl:{generation:'g',controlSequence:'0',planId:'plan'},requiredInitializerIds:['actors']};
   preparationInputs.push(new WeakRef(request.input));const root=await owner.sqliteBootstrap.create(request);if(owner.database.diagnostics().diagnostic)await owner.history.activate();const status=await owner.history.status();assert.equal(status.state,'clean');prepared={root,metadata:owner.database.metadata,database:owner.database,state:status.state};
  }});return prepared;
 }});
 const name=async handle=>{const view=await handle.database.snapshot({maxAgeMs:30000});try{return (await view.query({sql:'SELECT name FROM actors'},{maxRows:1,maxBytes:1024}))[0].name}finally{await view.close()}};
 try{
  const first=await host.ctx.runDirectory.create(plan('root-run',directory,'seeded-once'));const root=first.root;assert.equal(root.kind,'root');assert.equal(root.revision,'2');assert.ok((await stat(join(directory,root.root.path))).size>0);assert.equal(first.metadata,first.database.metadata);assert.equal(seeds.get('root-run'),1);
  if(global.gc){let released=false;for(let i=0;i<5;i++){await new Promise(resolve=>setImmediate(resolve));global.gc();if(!preparationInputs[0].deref()){released=true;break}}assert.equal(released,true,'large bootstrap input must be released after real root and generation entry')}
  let borrowed;await host.ctx.plugin({inject:['runDirectory'],apply(ctx){borrowed=ctx.runDirectory.acquire('root-run','write')}});const closing=first.close();await new Promise(resolve=>setImmediate(resolve));assert.equal(host.ctx.runDirectory.list()[0].closing,true);assert.equal(first.database.diagnostics().exited,false);await borrowed.release();await closing;assert.equal(first.database.diagnostics().exited,true);
  const reopened=await host.ctx.runDirectory.create(plan('root-run',directory,'must-not-reseed'));assert.equal(reopened.root.id,root.id);assert.equal(seeds.get('root-run'),1);assert.equal(reopened.database.diagnostics().diagnostic,false);
  const second=await host.ctx.runDirectory.create(plan('other-run',other,'isolated'));assert.equal(host.ctx.runDirectory.list().length,2);assert.equal(await name(reopened),'seeded-once');assert.equal(await name(second),'isolated');assert.notEqual(reopened.database.directory,second.database.directory);
  await reopened.close();assert.equal(await name(second),'isolated');await second.close();assert.deepEqual(host.ctx.runDirectory.list(),[]);assert.equal(host.ctx.profileLoader.trees().length,0);
 }finally{await host.close()}
});
