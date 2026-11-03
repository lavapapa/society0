import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const stage=fileURLToPath(new URL('../.build',import.meta.url));
const require=createRequire(stage+'/package.json');
const hostApi=await import(pathToFileURL(require.resolve('@society0/host')));
const {default:RandomStreams}=await import(pathToFileURL(require.resolve('@society0/core/random')));

async function sample(capacity,order,reverse=false){
 const host=await hostApi.createHost({boot:[]});
 const release=Promise.withResolvers(),entered=Promise.withResolvers();
 const trace=[];let active=0,peak=0;const jobs=[];
 try{
  await host.ctx.plugin(hostApi.OwnedResources);
  await host.ctx.plugin(hostApi.TaskSupervisor,{concurrency:capacity,maxPending:32,maxBytes:4096});
  for(const name of order){
   trace.push(name);
   if(name==='random')await host.ctx.plugin(RandomStreams,{algorithm:'philox4x32-10/v1',seed:'study-1'});
   else await host.ctx.plugin({name,apply(ctx){ctx.provide(name,{registered:true})}});
  }
  let owner;
  await host.ctx.plugin({inject:['tasks','random'],apply(ctx){owner=ctx}});
  const addresses=Array.from({length:12},(_,i)=>({run:'same-run',actor:'actor-'+i%4,operation:'same-operation',stream:'selection',index:String(i)}));
  if(reverse)addresses.reverse();
  for(const [i,address]of addresses.entries())jobs.push(owner.tasks.run('actor-draw-'+i,async()=>{
   active++;peak=Math.max(peak,active);if(active===capacity)entered.resolve();
   try{await release.promise;return [address.index,owner.random.at(address)]}finally{active--}
  },{bytes:1}));
  await entered.promise;release.resolve();const values=Object.fromEntries(await Promise.all(jobs));
  await Promise.all(jobs.map(job=>job.settled));
  assert.equal(active,0);assert.equal(peak,capacity);
  const snapshot=owner.tasks.snapshot();assert.equal(snapshot.activeCount,0);assert.equal(snapshot.pendingCount,0);
  return {values,description:owner.random.describe(),trace,peak};
 }finally{release.resolve();await Promise.allSettled(jobs.map(job=>job.settled));await host.close()}
}

test('PR11-T03 plugin registration order and actual task capacities preserve random addresses',async()=>{
 const serial=await sample(1,['noiseA','random','noiseB']);
 const parallel=await sample(4,['noiseB','noiseA','random'],true);
 assert.notDeepEqual(serial.trace,parallel.trace);
 assert.deepEqual(serial.values,parallel.values);
 assert.deepEqual(serial.description,{algorithm:'philox4x32-10/v1',mapping:'length-prefixed-utf8-seed-seq/v1',seed:'study-1'});
 assert.deepEqual(parallel.description,serial.description);
 console.log(JSON.stringify({case:'PR11-T03',capacities:[serial.peak,parallel.peak],addresses:Object.keys(serial.values).length,description:serial.description}));
});
