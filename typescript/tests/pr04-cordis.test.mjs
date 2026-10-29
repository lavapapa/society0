import {ArtifactsLocal,artifactsMigration} from '../.build/core/dist/artifacts-local/index.js';
import {SqliteBootstrap} from '../.build/core/dist/sqlite-bootstrap/index.js';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHost,OwnedResources,TaskSupervisor} from '../.build/host/dist/index.js';
import {ExecutionFence} from '../.build/core/dist/execution-fence/index.js';
import {SqliteDatabase} from '../.build/core/dist/sqlite/index.js';
import {Initializers} from '../.build/core/dist/initializers/index.js';
import {CompositionStrategies} from '../.build/host/dist/composition-strategies/index.js';
import slotStrategies from '../.build/host/dist/slot-strategies/index.js';
import {Slots} from '../.build/host/dist/slots/index.js';
const request=operationId=>({operationId,maxDurationMs:15000,maxStatements:30,maxInputBytes:65536,maxChangedRows:100,maxCapturedBytes:1048576});
async function setup(){const host=await createHost({boot:[]});await host.ctx.plugin(OwnedResources);await host.ctx.plugin(TaskSupervisor,{concurrency:4,maxBytes:33554432});await host.ctx.plugin(ExecutionFence);const directory=await mkdtemp(join(tmpdir(),'pr04-cordis-'));const provider=await host.ctx.plugin(SqliteDatabase,{directory,runId:'cordis',generation:'g',maxTransactionMs:20000});return {host,provider,directory}}
test('PR04 actual external notes and action repositories share tx and metadata identity',async()=>{
 const {host,provider,directory}=await setup();let ctx;
 try{
  await host.ctx.database.applyMigrations([{owner:'external-notes',from:0,to:1,source:{package:'external-notes',version:'1'},after:[],statements:[{sql:'CREATE TABLE notes(id INTEGER PRIMARY KEY,body TEXT NOT NULL)'},{sql:'CREATE TABLE actions(id INTEGER PRIMARY KEY,note INTEGER REFERENCES notes(id),label TEXT NOT NULL)'}]}]);
  const pluginFile=join(directory,'external-notes.mjs');await writeFile(pluginFile,`export default {inject:['database'],apply(ctx){ctx.provide('notes',{write:(tx,id,body)=>tx.execute({sql:'INSERT INTO notes VALUES (?,?)',parameters:[id,body]})});ctx.provide('actionReceipts',{write:(tx,id,note,label)=>tx.execute({sql:'INSERT INTO actions VALUES (?,?,?)',parameters:[id,note,label]})})}}`);
  await host.ctx.plugin((await import(pathToFileURL(pluginFile).href)).default);
  await host.ctx.plugin({inject:['database','executionFence','notes','actionReceipts'],apply(owner){ctx=owner}});
  const reservation=await ctx.executionFence.reserve({id:'action'});
  try{
   const result=await ctx.database.transaction(reservation.lease,request('action'),async tx=>{await ctx.notes.write(tx,1,'external');await ctx.actionReceipts.write(tx,1,1,'accepted');});assert.equal(result.receipt.revision,'1');
   await assert.rejects(ctx.database.transaction(reservation.lease,request('invalid-frontier'),tx=>ctx.database.metadata.complete(tx,{codec:'step/1',value:{n:NaN},logicalTime:1,completedCount:'1',pendingAcknowledgements:[]},['action'])),/LITERAL|finite|number/i);
   await ctx.database.transaction(reservation.lease,request('complete'),tx=>ctx.database.metadata.complete(tx,{codec:'step/1',value:{next:1},logicalTime:1,completedCount:'1',pendingAcknowledgements:[]},['action']));
   const view=await ctx.database.snapshot({maxAgeMs:30000});assert.equal((await ctx.database.metadata.read(view)).completedCount,'1');await view.close();
   assert.equal(ctx.database.metadata,host.ctx.database.metadata);
   await assert.rejects(ctx.database.transaction(reservation.lease,request('action'),async()=>{throw Error('duplicate callback ran')}),/OPERATION_EXISTS/);
  }finally{await reservation.release()}
  await provider.dispose();assert.equal(host.ctx.resources.closeReport().open,0);assert.equal(host.ctx.tasks.snapshot().activeCount,0);
 }finally{await host.close()}
});

test('PR04-T04 real initializer slots reject absent required IDs before any seed',async()=>{
 const {host}=await setup();let ctx,count=0;
 try{
  await host.ctx.plugin(CompositionStrategies);await host.ctx.plugin(slotStrategies);await host.ctx.plugin(Slots);await host.ctx.plugin(Initializers);
  await host.ctx.plugin({inject:['initializers'],apply(ctx){ctx.initializers.register({id:'actors',after:[],async initialize(){count++}})}});
  await host.ctx.plugin({inject:['initializers','database','executionFence'],apply(owner){ctx=owner}});
  const reservation=await ctx.executionFence.reserve({id:'seed'});
  const context={ctx,runId:'cordis',generation:'g',schema:[],initialTime:0,input:{},signal:reservation.signal,database:ctx.database,writeLease:reservation.lease,artifacts:undefined};
  try{await assert.rejects(ctx.initializers.prepare(context,['actors','missing']),/INITIALIZER_MISSING/);assert.equal(count,0);const seed=await ctx.initializers.prepare(context,['actors']);assert.deepEqual(seed.initializerIds,['actors']);assert.equal(count,1)}finally{await reservation.release()}
 }finally{await host.close()}
});

test('PR04 real dependent disposer reads old SQL binding before physical worker closes',async()=>{
 const {host,provider}=await setup();const database=host.ctx.database;let observed;
 await host.ctx.plugin({inject:['database'],apply(ctx){ctx.effect(()=>async()=>{assert.equal(database.diagnostics().exited,false);const view=await ctx.database.snapshot({maxAgeMs:30000});observed=await view.query({sql:'SELECT run_id FROM s0_identity'},{maxRows:1,maxBytes:1024});await view.close();assert.equal(database.diagnostics().exited,false)})}});
 try{await provider.dispose();assert.equal(observed[0].run_id,'cordis');assert.equal(database.diagnostics().exited,true);assert.equal(host.ctx.tasks.snapshot().activeCount,0)}finally{await host.close()}
});

test('PR04-T09 actual private bootstrap persists prepared frontier and never invents root',async()=>{
 const {host,directory}=await setup();let ctx,seeds=0;
 try{
  await host.ctx.plugin(CompositionStrategies);await host.ctx.plugin(slotStrategies);await host.ctx.plugin(Slots);await host.ctx.plugin(Initializers);
  await host.ctx.plugin(ArtifactsLocal,{directory:join(directory,'artifacts')});await host.ctx.plugin(SqliteBootstrap,{maxTransactionMs:15000,maxInputBytes:65536});
  await host.ctx.plugin({inject:['initializers'],apply(ctx){ctx.initializers.register({id:'actors',after:[],async initialize(context){seeds++;await context.database.transaction(context.writeLease,request('seed-actors'),tx=>tx.execute({sql:'INSERT INTO actors VALUES (1,?)',parameters:[context.input.name]}));return {operationIds:['seed-actors']}}})}});
  await host.ctx.plugin({inject:['sqliteBootstrap','database'],apply(owner){ctx=owner}});
  const create={operationId:'create-one',runId:'cordis',directory,input:{name:'prepared',rows:Array.from({length:8192},(_,id)=>({id,description:'x'.repeat(128)}))},migrations:[artifactsMigration,{owner:'actors',from:0,to:1,after:['artifacts-local'],source:{version:'1'},statements:[{sql:'CREATE TABLE actors(id INTEGER PRIMARY KEY,name TEXT NOT NULL)'}]}],initialFrontier:{codec:'step/1',value:0,logicalTime:0,completedCount:'0',pendingAcknowledgements:[]},initialControl:{generation:'g',controlSequence:'0',planId:'plan'},requiredInitializerIds:['actors']};
  const weakInput=new WeakRef(create.input);const seed=await ctx.sqliteBootstrap.prepare(create);create.input={name:'unused-retry'};assert.deepEqual(seed.initializerIds,['actors']);assert.equal(seeds,1);assert.equal((await ctx.sqliteBootstrap.probe(directory,'create-one')).status,'prepared');
  assert.deepEqual(await ctx.sqliteBootstrap.prepare(create),seed);assert.equal(seeds,1);
  if(global.gc){let released=false;for(let i=0;i<5;i++){await new Promise(resolve=>setImmediate(resolve));global.gc();if(!weakInput.deref()){released=true;break}}assert.equal(released,true,'initializer input graph must not survive preparation in a provider closure')}

  await assert.rejects(ctx.sqliteBootstrap.create(create),/ROOT_PUBLISHER_REQUIRED/);assert.equal(seeds,1);
  const view=await ctx.database.snapshot({maxAgeMs:30000});assert.equal((await ctx.database.metadata.read(view)).completedCount,'0');assert.equal((await view.query({sql:'SELECT name FROM actors'},{maxRows:1,maxBytes:1024}))[0].name,'prepared');await view.close();
 }finally{await host.close()}
});

test('PR04-T03 identical service keys in two native Cordis scopes own isolated SQL workers',async()=>{
 const host=await createHost({boot:[]});await host.ctx.plugin(OwnedResources);await host.ctx.plugin(TaskSupervisor);
 const setupRun=async runId=>{const directory=await mkdtemp(join(tmpdir(),'pr04-scope-'));const scope=host.ctx.isolate('database',Symbol(runId)).isolate('executionFence',Symbol(runId));await scope.plugin(ExecutionFence);const provider=await scope.plugin(SqliteDatabase,{directory,runId,generation:'g',maxTransactionMs:20000});let ctx;await scope.plugin({inject:['database','executionFence'],async apply(owner){ctx=owner;await owner.database.applyMigrations([{owner:'same-schema',from:0,to:1,after:[],source:{version:'1'},statements:[{sql:'CREATE TABLE records(id INTEGER PRIMARY KEY,name TEXT NOT NULL)'}]}]);const reservation=await owner.executionFence.reserve({id:'same-proposal'});try{await owner.database.transaction(reservation.lease,request('same-operation'),tx=>tx.execute({sql:'INSERT INTO records VALUES (1,?)',parameters:[runId]}))}finally{await reservation.release()}}});return {ctx,provider}};
 try{const [first,second]=await Promise.all([setupRun('first'),setupRun('second')]);const a=await first.ctx.database.snapshot({maxAgeMs:30000}),b=await second.ctx.database.snapshot({maxAgeMs:30000});assert.equal((await a.query({sql:'SELECT name FROM records'},{maxRows:1,maxBytes:1024}))[0].name,'first');assert.equal((await b.query({sql:'SELECT name FROM records'},{maxRows:1,maxBytes:1024}))[0].name,'second');assert.notEqual(first.ctx.database.directory,second.ctx.database.directory);await a.close();await first.provider.dispose();assert.equal((await b.query({sql:'SELECT name FROM records'},{maxRows:1,maxBytes:1024}))[0].name,'second');await b.close();await second.provider.dispose()}finally{await host.close()}
});
