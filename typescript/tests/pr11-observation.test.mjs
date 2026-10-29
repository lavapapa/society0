import test from 'node:test';import assert from 'node:assert/strict';import {fixture,load} from './pr11-support.mjs';import {createServer,request} from 'node:http';import {once} from 'node:events';import {setImmediate as tick,setTimeout as delay} from 'node:timers/promises';
test('PR11-T04/T06 recorded attempts replay offline and retain artifact after strategy disposal',async()=>{const f=await fixture();try{
 const id={lineage:'lineage-1',operationId:'external',attempt:0};const artifacts=await f.ctx.attempts.stageEvidence((async function*(){yield Buffer.from('recorded body')})(),'text/plain');await f.ctx.attempts.claim(id,{provider:'recorded'},[artifacts]);await f.ctx.attempts.settle(id,{answer:'kept',raw:{finish:'stop'}},[artifacts]);
 let replay;const policy=await f.ctx.plugin({inject:['replay'],apply(c){replay=c.replay}});const before=await replay.lookup(id);assert.equal(before.result.answer,'kept');await policy.dispose();const after=await f.ctx.replay.lookup(id);assert.deepEqual(after,before);assert.equal(f.ctx.replay.compare(before,after).equal,true);assert.equal(f.ctx.replay.compare(before,{changed:true}).equal,false);
 const chunks=[];for await(const chunk of f.ctx.replay.readArtifact(artifacts))chunks.push(chunk);assert.equal(Buffer.concat(chunks).toString(),'recorded body');await assert.rejects(f.ctx.replay.lookup({...id,operationId:'missing'}),/REPLAY_MISSING/);
 await f.ctx.attempts.claim({...id,operationId:'pending'},{x:1});await assert.rejects(f.ctx.replay.lookup({...id,operationId:'pending'}),/REPLAY_PENDING/);assert.equal((await f.ctx.history.current()).id,f.root.id);
 }finally{await f.close()}});
test('PR11-T05 stable snapshot pagination, read-only SQL and dirty/unknown classification',async()=>{const f=await fixture();try{
 const gateway=f.host.ctx.observationGateway;assert.equal((await gateway.status('source')).state,'clean');const first=await gateway.query('source',{sql:'SELECT id,v FROM note ORDER BY id',pageSize:2});assert.equal(first.rows.length,2);assert.deepEqual(first.rows[0].id,{integer:'0'});await f.write('change','updated');const second=await gateway.query('source',{cursor:first.cursor,pageSize:2});const last=await gateway.query('source',{cursor:second.cursor,pageSize:2});assert.equal(last.rows.length,1);assert.equal(last.cursor,undefined);assert.equal(first.revision,second.revision);assert.equal((await gateway.status('source')).state,'dirty');await assert.rejects(gateway.query('source',{sql:'DELETE FROM note',pageSize:1}),/syntax|read|SQLITE/);assert.equal(f.ctx.database.diagnostics().snapshots,0);
 await assert.rejects(gateway.query('source',{sql:'SELECT 1',pageSize:101}),/GATEWAY_PAGE_SIZE/);
 await gateway.query('source',{sql:'SELECT 1 AS value'});
 await assert.rejects(gateway.query('source',{pageSize:1}),/OBSERVATION_SQL/);
 await assert.rejects(gateway.query('source',{sql:'',pageSize:1}),/OBSERVATION_SQL/);
 const page=await f.ctx.observation.query({sql:'SELECT id FROM note ORDER BY id',pageSize:1});
 const concurrent=await Promise.allSettled([f.ctx.observation.query({cursor:page.cursor,pageSize:1}),f.ctx.observation.query({cursor:page.cursor,pageSize:1})]);
 assert.equal(concurrent.filter(x=>x.status==='fulfilled').length,1);assert.match(concurrent.find(x=>x.status==='rejected').reason.message,/OBSERVATION_CURSOR_BUSY/);
 await f.ctx.observation.closeCursor(page.cursor);
 const a=f.ctx.observation.subscribe(),b=f.ctx.observation.subscribe();await a.close();await a.close();await b.close();await b.close();await f.ctx.observation.refresh();
 await assert.rejects(gateway.subscribe('source',{signal:AbortSignal.abort()}));
 await exerciseHttp(f.host.ctx.observationGateway);
 f.ctx.executionFence.halt('outcome unknown');assert.equal((await gateway.status('source')).state,'unknown');assert.equal(f.host.ctx.runDirectory.list()[0].leases,0);
 }finally{await f.close()}});
test('PR11-T05 slow display subscription reports dropped sequences while SQL commits proceed',async()=>{const f=await fixture();let stream;try{stream=await f.host.ctx.observationGateway.subscribe('source');const initial=await stream.next();assert.equal(initial.value.status.state,'clean');for(let i=0;i<5;i++){await f.write('display-'+i,'change-'+i);await f.ctx.observation.refresh()}const event=await stream.next();assert.ok(event.value.dropped>=3);assert.ok(BigInt(event.value.sequence)>BigInt(initial.value.sequence)+1n);assert.equal((await f.ctx.database.probe('display-4')).status,'committed');await stream.close();assert.equal(f.host.ctx.runDirectory.list()[0].leases,0)}finally{await stream?.close();await f.close()}});
test('PR11-T07 actual HTTP disconnect cancels SQL and holds run lease until physical query ends', {timeout:300000},async()=>{const f=await fixture();const gateway=f.host.ctx.observationGateway;const completed=Promise.withResolvers();const server=createServer((req,res)=>{void gateway.handle(req,res).then(completed.resolve,completed.reject)});server.listen(0,'127.0.0.1');await once(server,'listening');let req;
 try{const body=JSON.stringify({sql:'WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) AS total FROM n',pageSize:1});req=request({host:'127.0.0.1',port:server.address().port,path:'/runs/source/query',method:'POST',headers:{'content-type':'application/json','content-length':Buffer.byteLength(body)}});req.on('error',()=>{});req.end(body);
 let started=false;for(let i=0;i<1000;i++){if(f.ctx.database.diagnostics().snapshots===1&&f.ctx.database.diagnostics().active===1){started=true;break}await tick()}assert.equal(started,true);assert.equal(f.host.ctx.runDirectory.list()[0].leases,1);let settled=false;void completed.promise.then(()=>{settled=true},()=>{settled=true});req.destroy();for(let i=0;i<1000&&!settled;i++){if(f.ctx.database.diagnostics().active>0)assert.equal(f.host.ctx.runDirectory.list()[0].leases,1,'physical SQL keeps the run lease');await tick()}await Promise.race([completed.promise,delay(10000,undefined,{ref:false}).then(()=>{throw Error('HTTP_CANCEL_DID_NOT_SETTLE')})]);assert.equal(f.ctx.database.diagnostics().snapshots,0);assert.equal(f.ctx.database.diagnostics().active,0);assert.equal(f.host.ctx.runDirectory.list()[0].leases,0);assert.equal(f.ctx.tasks.snapshot().activeCount,0);
 }finally{req?.destroy();await new Promise(resolve=>server.close(resolve));await f.close()}});

async function exerciseHttp(gateway){
 const tasks=[];const server=createServer((req,res)=>{const task=gateway.handle(req,res);tasks.push(task);void task.catch(()=>{})});
 server.listen(0,'127.0.0.1');await once(server,'listening');
 const send=(path,method='GET',value,stream=false)=>new Promise((resolve,reject)=>{
  const body=value===undefined?undefined:JSON.stringify(value);
  const req=request({host:'127.0.0.1',port:server.address().port,path,method,headers:body?{'content-type':'application/json','content-length':Buffer.byteLength(body)}:{}},res=>{
   const chunks=[];
   res.on('data',chunk=>{chunks.push(chunk);if(stream){resolve({status:res.statusCode,body:Buffer.concat(chunks).toString()});res.destroy();req.destroy()}});
   res.on('end',()=>resolve({status:res.statusCode,body:Buffer.concat(chunks).toString()}));
  });req.on('error',reject);req.end(body);
 });
 try{
  const status=await send('/runs/source/status');assert.equal(status.status,200);assert.equal(JSON.parse(status.body).runId,'source');
  assert.equal((await send('/missing')).status,404);
  assert.equal((await send('/runs/source/status','POST')).status,405);
  assert.equal((await send('/runs/source/query','POST',{sql:'DELETE FROM note',pageSize:1})).status,400);
  const event=await send('/runs/source/subscribe','GET',undefined,true);assert.equal(event.status,200);assert.equal(JSON.parse(event.body.trim()).status.runId,'source');
  await Promise.all(tasks);
 }finally{await new Promise(resolve=>server.close(resolve))}
}
