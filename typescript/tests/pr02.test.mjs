import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Worker } from 'node:worker_threads';
import { once } from 'node:events';
import { createServer, get } from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import { setImmediate as tick } from 'node:timers/promises';
import * as hostApi from '../.build/host/dist/index.js';

test('PR02-T01 real file leases belong to independent calling plugins', async () => {
  assert.equal(typeof hostApi.OwnedResources, 'function', 'PR02 resources export is required');
  const host = await hostApi.createHost({boot:[]});
  await host.ctx.plugin(hostApi.OwnedResources);
  const directory=await mkdtemp(join(tmpdir(),'pr02-resources-'));
  const leases=[];
  const plugin={inject:['resources'],async apply(ctx, name) {
    const lease=await ctx.resources.open(async()=>{
      const file=await open(join(directory,name),'w+');
      await file.writeFile(name);
      return {value:file,close:()=>file.close()};
    }, name);
    leases.push(lease);
  }};
  try {
    const first=await host.ctx.plugin(plugin,'first');
    const second=await host.ctx.plugin(plugin,'second');
    assert.equal(host.ctx.resources.closeReport().open,2);
    assert.deepEqual(host.ctx.resources.closeReport().residual.map(record=>record.owner.uid),[first.uid,second.uid]);
    await first.dispose();
    await assert.rejects(leases[0].value.stat(),/closed/);
    assert.ok(leases[0].close() instanceof Promise,'completed lease close remains awaitable Promise');
    assert.equal((await leases[1].value.stat()).size,6);
    assert.equal(host.ctx.resources.closeReport().open,1);
    await second.dispose();
    assert.equal(host.ctx.resources.closeReport().open,0);
    const report=await host.close();
    assert.equal(report.status,'closed');
  } finally {await host.close()}
});

test('PR02-T04 failed resource cleanup keeps precise residue and halts host', async()=>{
  assert.equal(typeof hostApi.OwnedResources,'function');
  const host=await hostApi.createHost({boot:[]});
  await host.ctx.plugin(hostApi.OwnedResources);
  const resources=host.ctx.resources;
  await host.ctx.plugin({inject:['resources'],apply(ctx){ctx.resources.use('broken-file-close',async()=>{throw Error('disk close failed')})}});
  await assert.rejects(host.close(),error=>{
    assert.equal(error.report.status,'halted');
    assert.match(JSON.stringify(error.report),/broken-file-close/);
    return true;
  });
  assert.equal(resources.closeReport().failed,1);
  assert.equal(host.status,'halted');
});


async function taskHost(config={concurrency:1,maxPending:2,maxBytes:100}) {
  assert.equal(typeof hostApi.TaskSupervisor,'function','PR02 TaskSupervisor export is required');
  const host=await hostApi.createHost({boot:[]});
  await host.ctx.plugin(hostApi.OwnedResources);
  await host.ctx.plugin(hostApi.TaskSupervisor,config);
  return host;
}

test('PR02-T02/T06 canceled SQLite await retains physical permit and drains before worker close', {timeout:10000},async()=>{
  const host=await taskHost(), gate=new Int32Array(new SharedArrayBuffer(4));
  let worker,connectionClosed=false,owner,heartbeat=0,followupStarted=false;
  const native=Promise.withResolvers(), result=Promise.withResolvers();
  const provider=await host.ctx.plugin({inject:['resources'],async apply(ctx){
    const lease=await ctx.resources.open(()=>{
      worker=new Worker(new URL('./pr02/sqlite-worker.mjs',import.meta.url),{workerData:gate.buffer});
      worker.on('message',message=>{if(message.type==='native-running')native.resolve();if(message.type==='result')result.resolve(message.total)});
      worker.once('error',result.reject);
      return {value:worker,async close(){worker.postMessage('close');await once(worker,'exit');connectionClosed=true}};
    },'sqlite-worker');
    ctx.provide('sqliteProbe',{query(){lease.value.postMessage('query');return result.promise}});
  }});
  const consumer=await host.ctx.plugin({inject:['tasks','sqliteProbe'],apply(ctx){owner=ctx}});
  const tasks=host.ctx.tasks, cancel=new AbortController();
  const first=owner.tasks.run('sqlite-native',()=>owner.sqliteProbe.query(),{signal:cancel.signal,bytes:40});
  const rejected=assert.rejects(first,/cancelled by caller/);
  const timer=setInterval(()=>{heartbeat++},1);
  let closing;
  try {
    await native.promise;
    cancel.abort(Error('cancelled by caller'));await rejected;
    assert.ok(first.settled instanceof Promise, 'physical settlement must be independently joinable');
    let physicallyDone=false; first.settled.then(()=>{physicallyDone=true});
    assert.equal(physicallyDone,false);
    const next=owner.tasks.run('after-cancel',async()=>{followupStarted=true},{bytes:20});
    const nextRejected=assert.rejects(next,/Abort|clos|drain/i);
    await tick();
    const snapshot=tasks.snapshot();
    assert.equal(snapshot.activeCount,1);assert.equal(snapshot.activeBytes,40);
    assert.equal(snapshot.pendingCount,1);assert.equal(snapshot.pendingBytes,20);
    assert.equal(followupStarted,false);
    closing=provider.dispose();await nextRejected;await tick();
    assert.equal(connectionClosed,false);assert.equal(tasks.snapshot().activeCount,1);
    assert.ok(heartbeat>0,'event loop responsive while native SQLite waits');
    Atomics.store(gate,0,1);Atomics.notify(gate,0);
    await closing;await first.settled;
    assert.equal(physicallyDone,true);
    assert.equal(connectionClosed,true);assert.equal(tasks.snapshot().activeCount,0);
    assert.equal(await result.promise,5000050001);
    assert.equal(consumer.state,hostApi.FiberState.PENDING);
  } finally {clearInterval(timer);Atomics.store(gate,0,1);Atomics.notify(gate,0);await closing;await host.close()}
});

test('PR02-T05 task requestClose returns before its own drain and pending queue rejects', {timeout:5000},async()=>{
  const host=await taskHost();let owner;
  const fiber=await host.ctx.plugin({inject:['tasks'],apply(ctx){owner=ctx}});
  const entered=Promise.withResolvers(),release=Promise.withResolvers();let returned=false,pendingStarted=false;
  const first=owner.tasks.run('self-close',async()=>{entered.resolve();await release.promise;owner.tasks.requestClose('self-close');returned=true;});
  const observed=first.catch(()=>{});
  try {
    await entered.promise;
    const pending=owner.tasks.run('pending',async()=>{pendingStarted=true});
    const rejected=assert.rejects(pending,/Abort|clos/i);
    release.resolve();await observed;await rejected;await fiber.dispose();
    assert.equal(returned,true);assert.equal(pendingStarted,false);
    assert.equal(host.ctx.tasks.snapshot().activeCount,0);
  }finally{release.resolve();await host.close()}
});

test('PR02 pending and byte admission limits report occupied work',async()=>{
  const host=await taskHost({concurrency:1,maxPending:1,maxBytes:10});let owner;
  await host.ctx.plugin({inject:['tasks'],apply(ctx){owner=ctx}});
  const entered=Promise.withResolvers(),release=Promise.withResolvers();
  const first=owner.tasks.run('active',async()=>{entered.resolve();await release.promise},{bytes:6});
  try{
    await entered.promise;
    await assert.rejects(async()=>owner.tasks.run('too-many-bytes',async()=>{}, {bytes:5}),/TASK_BYTE_LIMIT/);
    const pending=owner.tasks.run('pending',async()=>{}, {bytes:4});
    await assert.rejects(async()=>owner.tasks.run('overflow',async()=>{}),/TASK_PENDING_LIMIT/);
    const rejected=assert.rejects(pending,/Abort|clos|drain/i);
    const closing=owner.tasks.drain('budget check');
    await rejected;assert.equal(host.ctx.tasks.snapshot().activeBytes,6);
    release.resolve();await closing;await first.catch(()=>{});
  }finally{release.resolve();await host.close()}
});


test('PR02-T03 local HTTP response physically drains before resource provider closes', {timeout:5000}, async()=>{
  const host=await taskHost(), entered=Promise.withResolvers(), release=Promise.withResolvers();
  let owner,serverClosed=false,bodyFinished=false;
  const provider=await host.ctx.plugin({inject:['resources'],async apply(ctx){
    const lease=await ctx.resources.open(async()=>{
      const server=createServer(async(_req,res)=>{res.writeHead(200);res.write('first');await release.promise;res.end('last')});
      server.listen(0,'127.0.0.1');await once(server,'listening');
      return {value:`http://127.0.0.1:${server.address().port}`,close:()=>new Promise((resolve,reject)=>server.close(error=>{serverClosed=true;error?reject(error):resolve()}))};
    },'loopback-http');
    ctx.provide('httpProbe',lease.value);
  }});
  await host.ctx.plugin({inject:['tasks','httpProbe'],apply(ctx){owner=ctx}});
  const cancel=new AbortController(),tasks=host.ctx.tasks;
  const request=owner.tasks.run('http-body',()=>new Promise((resolve,reject)=>{
    const req=get(owner.httpProbe,res=>{
      let body='';res.on('data',chunk=>{body+=chunk;entered.resolve()});
      res.on('end',()=>{bodyFinished=true;resolve(body)});res.on('error',reject);
    });req.on('error',reject);
  }),{signal:cancel.signal,bytes:8});
  const rejected=assert.rejects(request,/http cancel/);let closing;
  try {
    await entered.promise;cancel.abort(Error('http cancel'));await rejected;
    closing=provider.dispose();await tick();
    assert.equal(tasks.snapshot().activeCount,1);assert.equal(bodyFinished,false);assert.equal(serverClosed,false);
    release.resolve();await closing;
    assert.equal(bodyFinished,true);assert.equal(serverClosed,true);assert.equal(tasks.snapshot().activeBytes,0);
  }finally{release.resolve();await closing;await host.close()}
});

test('Host timeout reports live resource and later real drain preserves halted state', {timeout:5000},async()=>{
  const host=await taskHost(),entered=Promise.withResolvers(),release=Promise.withResolvers();
  await host.ctx.plugin({inject:['resources'],apply(ctx){ctx.resources.use('slow-close',async()=>{entered.resolve();await release.promise})}});
  const closing=host.close({timeoutMs:10});
  const outcome=closing.then(report=>({report}),error=>({error}));
  try{
    await entered.promise;
    const result=await Promise.race([outcome,delay(100,{missingTimeout:true})]);
    assert.ok(result.error,'close deadline must report incomplete cleanup');
    assert.equal(result.error.report.drained,false);assert.equal(result.error.report.status,'halted');
    assert.equal(result.error.report.resources.closing,1);
    assert.match(JSON.stringify(result.error.report),/slow-close/);
    release.resolve();
    await assert.rejects(host.close(),error=>{assert.equal(error.report.drained,true);return true});
  }finally{release.resolve();await outcome;await host.close().catch(()=>{})}
});


test('physical failure after awaiter cancellation remains diagnostic evidence',async()=>{
  const host=await taskHost();let owner;
  await host.ctx.plugin({inject:['tasks'],apply(ctx){owner=ctx}});
  const entered=Promise.withResolvers(),release=Promise.withResolvers(),cancel=new AbortController();
  const waiter=owner.tasks.run('late-native-error',async()=>{entered.resolve();await release.promise;throw Error('physical native failure')},{signal:cancel.signal});
  const rejected=assert.rejects(waiter,/cancel waiter/);
  try{
    await entered.promise;cancel.abort(Error('cancel waiter'));await rejected;
    release.resolve();await owner.tasks.drain('finished');
    assert.ok(host.diagnostics.snapshot().errors.some(error=>error.message.includes('physical native failure')));
  }finally{release.resolve();await host.close()}
});

test('root-owned resource failure remains reported after service plugin teardown',async()=>{
  const host=await taskHost();
  host.ctx.resources.use('root-close-fault',()=>{throw Error('root resource failed')});
  await assert.rejects(host.close(),error=>{
    assert.equal(error.report.resources.failed,1);
    assert.ok(error.report.diagnostics.errors.some(record=>record.label==='root-close-fault' && record.message==='root resource failed'));
    return true;
  });
});

test('resource acquisition started before disposal is joined and closed once',async()=>{
  const host=await taskHost(),entered=Promise.withResolvers(),release=Promise.withResolvers();let closed=0;
  const fiber=host.ctx.plugin({inject:['resources'],async apply(ctx){
    await ctx.resources.open(async()=>{entered.resolve();await release.promise;return {value:1,close(){closed++}}},'slow-open');
  }});
  await entered.promise;const closing=fiber.dispose();
  try{await tick();assert.equal(closed,0);assert.equal(host.ctx.resources.closeReport().opening,1);release.resolve();await closing;assert.equal(closed,1)}
  finally{release.resolve();await closing;await host.close()}
});

test('task drain of one caller preserves another caller and rejects self-join',async()=>{
  const host=await taskHost(),release=Promise.withResolvers(),entered=Promise.withResolvers();let left,right;
  const a=await host.ctx.plugin({inject:['tasks'],apply(ctx){left=ctx}});
  await host.ctx.plugin({inject:['tasks'],apply(ctx){right=ctx}});
  const first=left.tasks.run('left',async()=>{entered.resolve();await release.promise});
  await entered.promise;
  const closing=a.dispose();await first.catch(()=>{});
  const value=await right.tasks.run('right',async()=>{
    assert.throws(()=>right.tasks.drain('self'),/TASK_SELF_JOIN/);
    return 42;
  });
  try {assert.equal(value,42);assert.equal(host.ctx.tasks.snapshot().activeCount,1);release.resolve();await closing}
  finally {release.resolve();await closing;await host.close()}
});


test('native structural child cleanup can query retired provider through prebound physical scope', {timeout:5000},async()=>{
  const host=await taskHost();
  assert.equal(typeof host.ctx.tasks.bind,'function','provider-bound task scope required for native cleanup queries');
  const gate=new Int32Array(new SharedArrayBuffer(4));gate[0]=1;
  let closed=false,finalResult,scope,child;
  const result=Promise.withResolvers();
  const provider=await host.ctx.plugin({inject:['resources','tasks'],async apply(ctx){
    scope=ctx.tasks.bind();
    const lease=await ctx.resources.open(()=>{
      const worker=new Worker(new URL('./pr02/sqlite-worker.mjs',import.meta.url),{workerData:gate.buffer});
      worker.on('message',message=>{if(message.type==='result')result.resolve(message.total)});worker.once('error',result.reject);
      return {value:worker,async close(){await scope.drain('database closing');worker.postMessage('close');await once(worker,'exit');closed=true}};
    },'cleanup-query-worker');
    ctx.provide('cleanupSql',{query:()=>scope.run('cleanup-sql',()=>{lease.value.postMessage('query');return result.promise})});
    child=ctx.plugin(owner=>async()=>{assert.equal(closed,false);finalResult=await owner.cleanupSql.query();assert.equal(closed,false)});
  }});
  try {
    await child.await();await provider.dispose();
    assert.equal(finalResult,5000050001);assert.equal(closed,true);
    assert.throws(()=>scope.run('late-query',async()=>1),/TASK_OWNER_CLOSING/);
  }finally{await host.close()}
});

test('resource closeTimeoutMs supplies Host default without completing physical cleanup',async()=>{
  const host=await hostApi.createHost({boot:[]}),release=Promise.withResolvers();
  await host.ctx.plugin(hostApi.OwnedResources,{closeTimeoutMs:5});
  await host.ctx.plugin({inject:['resources'],apply(ctx){ctx.resources.use('configured-slow-close',()=>release.promise)}});
  const closing=host.close().then(report=>({report}),error=>({error}));
  try {
    const result=await Promise.race([closing,delay(100,{missingTimeout:true})]);
    assert.ok(result.error,'resource closeTimeoutMs must be effective');
    assert.equal(result.error.report.drained,false);assert.equal(result.error.report.resources.closing,1);
  } finally {release.resolve();await closing;await host.close().catch(()=>{})}
});


test('PR02-T04 setup failure after real acquisition closes the acquired file',async()=>{
  const host=await taskHost(),directory=await mkdtemp(join(tmpdir(),'pr02-setup-'));let file;
  const fiber=host.ctx.plugin({inject:['resources'],async apply(ctx){
    await ctx.resources.open(async()=>{file=await open(join(directory,'acquired'),'w+');return {value:file,close:()=>file.close()}},'setup-acquired-file');
    throw Error('after acquisition setup failed');
  }});
  await assert.rejects(fiber.await(),/after acquisition setup failed/);
  await assert.rejects(file.stat(),/closed/);
  assert.equal(host.ctx.resources.closeReport().open,0);
  await assert.rejects(host.close(),hostApi.HostCloseError);
});

test('PR02-T04 repeated resource disposer joins once and cannot release successor identity',async()=>{
  const host=await taskHost(),entered=Promise.withResolvers(),release=Promise.withResolvers();let owner,closes=0;
  await host.ctx.plugin({inject:['resources'],apply(ctx){owner=ctx}});
  const dispose=owner.resources.use('same-label',async()=>{closes++;entered.resolve();await release.promise});
  const first=dispose();await entered.promise;let secondDone=false;
  const second=Promise.resolve(dispose()).then(()=>{secondDone=true});
  try{
    await tick();assert.equal(secondDone,false);assert.equal(closes,1);
    release.resolve();await first;await second;
    const successor=owner.resources.use('same-label',()=>{closes++});
    await dispose();assert.equal(host.ctx.resources.closeReport().open,1);assert.equal(closes,1);
    await successor();assert.equal(closes,2);assert.equal(host.ctx.resources.closeReport().open,0);
  }finally{release.resolve();await first;await second;await host.close()}
});
