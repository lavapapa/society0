import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';import {createRequire} from 'node:module';import {pathToFileURL} from 'node:url';
import * as H from '../.build/host/dist/index.js';import {compileLayers} from '../.build/host/dist/profile-loader/index.js';
import Slots from '../.build/host/dist/slots/index.js';import CompositionStrategies from '../.build/host/dist/composition-strategies/index.js';import SlotStrategies from '../.build/host/dist/slot-strategies/index.js';import ProfileLoader from '../.build/host/dist/profile-loader/index.js';import ModuleResolver from '../.build/host/dist/module-resolver/index.js';import ConfigResolvers from '../.build/host/dist/config-resolvers/index.js';
import SqliteDatabase from '../.build/core/dist/sqlite/index.js';import Fence from '../.build/core/dist/execution-fence/index.js';import Artifacts,{artifactsMigration} from '../.build/core/dist/artifacts-local/index.js';import Initializers from '../.build/core/dist/initializers/index.js';import Bootstrap from '../.build/core/dist/sqlite-bootstrap/index.js';import History from '../.build/core/dist/sqlite-history/index.js';
test('public bootstrap Random scopes feed real Initializers and persisted SQL seed',{timeout:90000},async()=>{
 const require=createRequire(resolve(process.env.RANDOM_STAGE??'.build')+'/package.json');const R=await import(pathToFileURL(require.resolve('@society0/core/random')));const native=require('@society0/random-native');
 assert.deepEqual(R.metadata.allowedScopes,['generation','bootstrap']);assert.equal(R.metadata.scope,'generation');assert.ok(R.metadata.configPolicy.every(field=>field.mode==='new-run'));
 const host=await H.createHost({boot:[]});const ctx=host.ctx;const directory=await mkdtemp(join(tmpdir(),'pr11-bootstrap-rng-'));const address={run:'init',actor:'bootstrap',operation:'seed',stream:'world',index:'0'};
 try{
  for(const P of [H.OwnedResources,H.TaskSupervisor,ModuleResolver,ConfigResolvers,ProfileLoader,CompositionStrategies,SlotStrategies,Slots,Fence])await ctx.plugin(P);
  await ctx.plugin(SqliteDatabase,{directory,runId:'init',generation:'g1',maxTransactionMs:20000});await ctx.plugin(Artifacts,{directory:join(directory,'artifacts')});await ctx.plugin(Initializers);await ctx.plugin(Bootstrap,{maxTransactionMs:15000});await ctx.plugin(History,{directory,runId:'init',generation:'g1',controlSequence:'0'});
  const entries=[{id:'run-scope',name:'run',group:true,lifetime:'run',config:[{id:'bootstrap-scope',name:'bootstrap',group:true,lifetime:'bootstrap',config:[{id:'bootstrap-random',name:'random',config:{algorithm:'philox4x32-10/v1',seed:'study-1'}}]}]}];
  const plan=await compileLayers([{kind:'profile',uri:'fixture:bootstrap-random',version:'1',patches:[{insert:entries}]}],{random:{plugin:R.default,metadata:R.metadata,api:R.api}});
  const tree=await ctx.profileLoader.mount(plan,ctx);
  await tree.entries.get('bootstrap-scope').ctx.plugin({
   inject:['random','initializers'],
   apply(c){
    c.initializers.register({
     id:'scientific-seed',after:[],
     async initialize(seed){
      const value=c.random.at(address);
      await seed.database.transaction(seed.writeLease,{operationId:'seed-random',maxDurationMs:15000,maxStatements:4,maxInputBytes:4096,maxChangedRows:4,maxCapturedBytes:65536},tx=>tx.execute({sql:'INSERT INTO scientific_seed VALUES(?,?)',parameters:[BigInt(value.uint32),value.uniform]}));
      return {operationIds:['seed-random']};
     }
    });
   }
  });
  await ctx.sqliteBootstrap.create({operationId:'create-init',runId:'init',directory,input:{},migrations:[artifactsMigration,{owner:'rng-seed',from:0,to:1,after:['artifacts-local'],source:{entry:'PR11'},statements:[{sql:'CREATE TABLE scientific_seed(u INTEGER PRIMARY KEY,v REAL NOT NULL)'}]}],initialFrontier:{codec:'staged/1',value:'0',logicalTime:'0',completedCount:'0',pendingAcknowledgements:[]},initialControl:{generation:'g1',controlSequence:'0',planId:'bootstrap'},requiredInitializerIds:['scientific-seed']});
  const snapshot=await ctx.database.snapshot({maxAgeMs:30000});try{const [row]=await snapshot.query({sql:'SELECT u,v FROM scientific_seed'},{maxRows:1,maxBytes:4096});const expected=native.at('study-1',address);assert.equal(row.u,BigInt(expected.uint32));assert.equal(row.v,expected.uniform)}finally{await snapshot.close()}
  await tree.close();
 }finally{await host.close()}
});
