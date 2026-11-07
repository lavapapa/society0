import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,inventoryProvider,acquire,readFile,act,table} from './b7-support.mjs';

const publicFile='/public/market/prices/2026-11.jsonl';

// B7：act 在同一事务写入领域表与 /public 投影后，/self 在同一个 moment 内立即可见；
// /public 仍按 R_m 冻结，要到下一个 moment 才可见。
test('act写入后同一moment内/self立即可见，/public到m+1才可见',async()=>{
 const run=await setup();let session;
 try{
  run.host.ctx.publicSelf.provide(inventoryProvider());
  await run.openMoment('m','b7-ryw-open-m');
  session=await acquire(run,'A','m','b7-ryw-read');
  const before=await readFile(session.value,'/self/inventory.jsonl');
  assert.equal(before.text,'','act 之前库存为空');

  await act(run,{actorId:'A',id:'widget',qty:7,publicRow:{id:'p1',at:'2026-11-01',price:'1.50',note:'act'}});

  const after=await readFile(session.value,'/self/inventory.jsonl');
  assert.equal(after.text,'{"id":"widget","qty":7}\n','同一 moment 内 /self 必须读到本主体刚写入的行');
  assert.match(after.node.objectId,/self:file:/);

  await assert.rejects(()=>session.value.stat(publicFile),error=>error.code==='EXPLORATION_NOT_VISIBLE','m 的 R_m 早于 act 提交，/public 不应看到该文件');
  await session.release();session=undefined;

  await run.openMoment('m+1','b7-ryw-open-m1');
  const next=await acquire(run,'A','m+1','b7-ryw-read-m1');
  try{
   const published=await readFile(next.value,publicFile);
   assert.equal(published.text,'{"id":"p1","at":"2026-11-01","price":"1.50","note":"act"}\n','m+1 的 R_m 覆盖 act 提交，/public 应看到该行');
  }finally{await next.release()}
 }finally{
  try{await session?.release()}catch{}
  await run.close();
 }
});
