import test from 'node:test';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
import {fixture} from './pr10-support.mjs';

test('PR10-T05 functional fixture waits for real SQLite physical close',async()=>{
 const f=await fixture(),resources=f.ctx.resources,tasks=f.ctx.tasks;
 const rpc=f.ctx.database.database.rpc,worker=rpc.worker,post=worker.postMessage;
 const entered=Promise.withResolvers();let held,restored=false;
 function release(){if(restored)return;restored=true;worker.postMessage=post;if(held)post.call(worker,held.message,...held.args)}
 worker.postMessage=function(message,...args){
  if(message.op==='close'){assert.equal(held,undefined);held={message,args};entered.resolve();return}
  return post.call(this,message,...args);
 };
 const outcome=f.host.close().then(value=>({value}),error=>({error}));
 try{
  const first=await Promise.race([entered.promise.then(()=>({entered:true})),outcome]);
  assert.equal(first.entered,true,'target SQLite close must be entered before any outcome');
  const started=performance.now();await delay(31000);const heldMs=performance.now()-started;
  assert.equal(rpc.snapshot().exited,false);
  assert(resources.closeReport().residual.some(x=>x.label==='sqlite.worker'&&x.state==='closing'));
  release();const result=await outcome;await rpc.exited;
  const physical=await f.host.close({timeoutMs:120000}).then(value=>value,error=>error.report);
  assert.equal(physical.drained,true);assert.equal(rpc.snapshot().exited,true);
  const final=resources.closeReport();assert.equal(final.opening+final.open+final.closing+final.failed,0);
  assert.equal(tasks.snapshot().activeCount+tasks.snapshot().pendingCount,0);
  console.log(JSON.stringify({case:'PR10-T05',heldMs,fixtureCloseTimeoutMs:resources.closeTimeoutMs,returned:result.error?.report??result.value,physical,final}));
  assert.equal(result.error,undefined);assert.equal(result.value.status,'closed');
 }finally{release();await f.host.close({timeoutMs:120000}).catch(()=>{})}
});
// PR10-T05：功能fixture等待真实Worker物理退出；产品默认关闭期限另有合同验证。
