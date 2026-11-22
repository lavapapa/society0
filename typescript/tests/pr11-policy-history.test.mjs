// cases: PR11-T04 PR11-T06
import test from 'node:test';
import assert from 'node:assert/strict';
import {Worker} from 'node:worker_threads';
import {once} from 'node:events';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {fixture,resolvePackage,load} from './pr11-support.mjs';
test('PR11-T06 real domain policy retires while published history and its session events restore',{timeout:300000},async()=>{
 const f=await fixture();let policy,caller,domain,retired=false;
 try{
  const session={actorId:'domain',momentId:'m'},identity={session,operationId:'domain-result',attempt:0};
  policy=await f.ctx.plugin({inject:['sessions'],apply(ctx){ctx.effect(()=>()=>{retired=true});ctx.provide('domainHistoryPolicy',{async execute(){
   await f.write('domain-policy-write','domain-value-kept');
   ctx.sessions.append(session,'domain/result',{operationId:identity.operationId,attempt:0,answer:'durable-answer',raw:{finish:'stop',original:{nested:true}}});
  }})}});
  caller=await f.ctx.plugin({inject:['domainHistoryPolicy'],apply(ctx){domain=ctx.domainHistoryPolicy}});
  await domain.execute();const before=await f.ctx.replay.lookup(identity);
  assert.equal(before.data.answer,'durable-answer');assert.equal(before.data.raw.original.nested,true);
  f.ctx.history.bindQuiescence(()=>f.ctx.tasks.drain());const lease=await f.ctx.executionFence.seal();
  const point=await f.ctx.history.publish(lease,{operationId:'publish-domain-point',id:'domain-point',parentId:f.root.id,expectedRevision:await f.ctx.database.readRevision(),requiredOperations:['domain-policy-write']});
  if(f.ctx.executionFence.snapshot().state==='sealed')f.ctx.executionFence.resume(lease);
  await caller.dispose();await policy.dispose();assert.equal(retired,true);
  assert.deepEqual(await f.ctx.replay.lookup(identity),before);assert.equal((await f.ctx.history.current()).id,point.id);
  const target=join(f.home,'retired-policy-branch');await f.ctx.history.fork({operationId:'fork-retired-policy',pointId:point.id,targetRunId:'retired-policy-branch',targetDirectory:target});
  const source=`import {parentPort,workerData} from 'node:worker_threads';const {openConnection}=await import(workerData.module);const db=openConnection({path:workerData.path,readOnly:true});let value;try{db.beginSnapshot({maxAgeMs:10000});value=db.query('SELECT v FROM note WHERE id=0',[],{maxRows:1,maxBytes:4096})[0].v;db.endSnapshot()}finally{db.close()}parentPort.postMessage(value);parentPort.close();`;
  const worker=new Worker(new URL('data:text/javascript,'+encodeURIComponent(source)),{workerData:{module:pathToFileURL(resolvePackage('@society0/sqlite-native')).href,path:join(target,'world.sqlite')}});
  const [[value],[code]]=await Promise.all([once(worker,'message'),once(worker,'exit')]);assert.equal(code,0);assert.equal(value,'domain-value-kept');
  const {SessionLog}=await load('@society0/core/session-log');const inherited=[];for await(const event of new SessionLog(join(target,'sessions')).read(session))inherited.push(event.data.answer);
  assert.deepEqual(inherited,['durable-answer']);
  assert.equal(f.ctx.tasks.snapshot().activeCount,0);assert.equal(f.ctx.tasks.snapshot().pendingCount,0);assert.equal(f.ctx.database.diagnostics().snapshots,0);
  console.log(JSON.stringify({gate:'PR11-T06-public-history',point:point.id,policyRemoved:true,restored:value,target}));
 }finally{await caller?.dispose();await policy?.dispose();await f.close()}
});
