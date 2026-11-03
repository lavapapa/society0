// cases: PR11-T04 PR11-T06
import test from 'node:test';
import assert from 'node:assert/strict';
import {Worker} from 'node:worker_threads';
import {once} from 'node:events';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {fixture,resolvePackage} from './pr11-support.mjs';
test('PR11-T06 real domain policy retires while published history and attempt objects restore',{timeout:300000},async()=>{
 const f=await fixture();let policy;
 try{
  const identity={lineage:'lineage-1',operationId:'domain-result',attempt:0};
  policy=await f.ctx.plugin({inject:['attempts'],apply(ctx){ctx.provide('domainHistoryPolicy',{async execute(){
   const artifact=await ctx.attempts.stageEvidence((async function*(){yield Buffer.from('domain original evidence')})(),'text/plain');
   await ctx.attempts.claim(identity,{domain:'author-plugin',input:{original:true}},[artifact]);
   await f.write('domain-policy-write','domain-value-kept');
   await ctx.attempts.settle(identity,{answer:'durable-answer',raw:{finish:'stop',original:{nested:true}}},[artifact]);return artifact;
  }})}});
  const artifact=await f.ctx.domainHistoryPolicy.execute();const before=await f.ctx.replay.lookup(identity);
  assert.equal(before.result.answer,'durable-answer');assert.equal(before.result.raw.original.nested,true);
  f.ctx.history.bindQuiescence(()=>f.ctx.tasks.drain());const lease=await f.ctx.executionFence.seal();
  const point=await f.ctx.history.publish(lease,{operationId:'publish-domain-point',id:'domain-point',parentId:f.root.id,expectedRevision:await f.ctx.database.readRevision(),requiredOperations:['domain-policy-write']});
  if(f.ctx.executionFence.snapshot().state==='sealed')f.ctx.executionFence.resume(lease);
  await policy.dispose();assert.equal(f.ctx.domainHistoryPolicy,undefined);
  assert.deepEqual(await f.ctx.replay.lookup(identity),before);assert.equal((await f.ctx.history.current()).id,point.id);
  const chunks=[];for await(const chunk of f.ctx.replay.readArtifact(artifact))chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString(),'domain original evidence');
  const target=join(f.home,'retired-policy-branch');await f.ctx.history.fork({operationId:'fork-retired-policy',pointId:point.id,targetRunId:'retired-policy-branch',targetDirectory:target});
  const source=`import {parentPort,workerData} from 'node:worker_threads';const {openConnection}=await import(workerData.module);const db=openConnection({path:workerData.path,readOnly:true});let value;try{db.beginSnapshot({maxAgeMs:10000});value=db.query('SELECT v FROM note WHERE id=0',[],{maxRows:1,maxBytes:4096})[0].v;db.endSnapshot()}finally{db.close()}parentPort.postMessage(value);parentPort.close();`;
  const worker=new Worker(new URL('data:text/javascript,'+encodeURIComponent(source)),{workerData:{module:pathToFileURL(resolvePackage('@society0/sqlite-native')).href,path:join(target,'world.sqlite')}});
  const [[value],[code]]=await Promise.all([once(worker,'message'),once(worker,'exit')]);assert.equal(code,0);assert.equal(value,'domain-value-kept');
  assert.equal(f.ctx.tasks.snapshot().activeCount,0);assert.equal(f.ctx.tasks.snapshot().pendingCount,0);assert.equal(f.ctx.database.diagnostics().snapshots,0);
  console.log(JSON.stringify({gate:'PR11-T06-public-history',point:point.id,policyRemoved:true,artifactBytes:Buffer.concat(chunks).length,restored:value,target}));
 }finally{await policy?.dispose();await f.close()}
});
