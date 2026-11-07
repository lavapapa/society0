import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';
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
import PublicSelf from '../.build/core/dist/public-self/index.js';
import MomentViews,{momentViewsMigration} from '../.build/core/dist/moment-views/index.js';
import {Threads,threadMigration} from '../.build/core/dist/thread/index.js';
import {Perceptions,perceptionMigration} from '../.build/core/dist/perception/index.js';

const profiles=['event-sqlite','rule-sqlite','social-codex-sqlite','social-sqlite'];
const flatten=entries=>entries.flatMap(entry=>entry.group?flatten(entry.config):[entry]);
const page={maxItems:64,maxResponseBytes:1<<20};
const request=operationId=>({operationId,maxDurationMs:120000,maxStatements:200000,maxInputBytes:16777216,maxChangedRows:100000,maxCapturedBytes:33554432});
const plugins={'@society0/core/exploration':Exploration,'@society0/core/public':Public,'@society0/core/public-self':PublicSelf,'@society0/core/moment-views':MomentViews};

async function entries(name){
 const profile=JSON.parse(await readFile(new URL('../profiles/'+name+'.json',import.meta.url),'utf8'));
 return flatten(profile.patches[0].insert);
}

async function assemble(name,composition){
 const base=await mkdtemp(join(tmpdir(),'b9-mounts-')),directory=join(base,'run');
 const host=await createHost({boot:[]});
 try{
  await host.ctx.plugin(OwnedResources);
  await host.ctx.plugin(TaskSupervisor,{concurrency:8,maxBytes:134217728});
  await host.ctx.plugin(CompositionStrategies);
  await host.ctx.plugin(strategies);
  await host.ctx.plugin(Slots);
  await host.ctx.plugin(ExecutionFence);
  const sqlite=composition.find(item=>item.name==='@society0/core/sqlite')?.config??{};
  await host.ctx.plugin(SqliteDatabase,{maxTransactionMs:120000,busyTimeoutMs:120000,maxSnapshotAgeMs:300000,maxSnapshots:16,readerWorkers:2,maxInputBytes:16777216,maxQueuedBytes:67108864,maxQueryBytes:16777216,maxQueryRows:100000,maxInlineBytes:16777216,...sqlite,directory,runId:'b9-'+name,generation:'g1'});
  const artifacts=composition.find(item=>item.name==='@society0/core/artifacts-local')?.config??{};
  await host.ctx.plugin(ArtifactsLocal,{...artifacts,directory:join(directory,'artifacts')});
  await host.ctx.database.applyMigrations([artifactsMigration,publicMigration({consumer:'b9-mounts'}),momentViewsMigration({consumer:'b9-mounts'}),threadMigration({consumer:'b9-mounts'}),perceptionMigration({consumer:'b9-mounts'})]);
  await host.ctx.plugin(Threads);
  await host.ctx.plugin(Perceptions);
  for(const entry of composition)await host.ctx.plugin(plugins[entry.name],entry.config??{});
  let ctx;await host.ctx.plugin({inject:['database','executionFence','exploration','public','publicSelf','momentViews','perceptions'],apply(c){ctx=c}});
  return {host,ctx,base};
 }
 catch(error){await host.close();throw error}
}

for(const name of profiles)test('B9 '+name+' assembles /public and /self mounts without /world',async()=>{
 const composition=await entries(name);
 const mountWorld=composition.find(item=>item.name==='@society0/core/mount-world');
 assert.equal(mountWorld,undefined,name+' must not ship mount-world');
 for(const required of Object.keys(plugins))assert.ok(composition.some(item=>item.name===required),name+' must ship '+required);
 const ordered=composition.filter(item=>plugins[item.name]);
 const run=await assemble(name,ordered);
 try{
  const reservation=await run.ctx.executionFence.reserve({id:'b9-mounts'});
  try{
   const context={ctx:run.ctx,database:run.ctx.database,writeLease:reservation.lease,actorId:'a',momentId:'m',logicalTime:1,runId:'b9-'+name,generation:'g1',operationId:'b9-mounts',signal:reservation.signal};
   const scope={actorId:'a',momentId:'m',query:null};
   const session=await run.ctx.exploration.acquire(scope,context);
   try{
    const roots=await session.value.list('/',page);
    const paths=roots.items.map(item=>item.path);
    assert.ok(paths.includes('/public'),name+' root listing has /public: '+paths.join(','));
    assert.ok(paths.includes('/self'),name+' root listing has /self: '+paths.join(','));
    assert.equal(paths.includes('/world'),false,name+' root listing has no /world');
    await assert.rejects(session.value.stat('/world'),error=>error.code==='EXPLORATION_MOUNT_MISSING',name+' /world is not mounted');
   }finally{await session.release()}
   const source=await run.ctx.perceptions.acquire(['public.changes'],context);
   await source.release();
  }finally{await reservation.release()}
 }finally{await run.host.close()}
});
