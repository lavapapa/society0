import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,inventoryProvider,acquire,readFile} from './b7-support.mjs';

// B7：/self 只读；会话的写入与变更动作必须返回 EXPLORATION_READ_ONLY。
test('写/self返回EXPLORATION_READ_ONLY',async()=>{
 const run=await setup();let session;
 try{
  run.host.ctx.publicSelf.provide(inventoryProvider());
  await run.openMoment('m','b7-ro-open');
  session=await acquire(run,'A','m','b7-ro-read');

  async function* bytes(){yield Buffer.from('tamper\n')}
  await assert.rejects(()=>session.value.writeFile('/self/inventory.jsonl',bytes(),null,{}),error=>error.code==='EXPLORATION_READ_ONLY');

  const root=await session.value.stat('/self');
  await assert.rejects(()=>session.value.mutate({kind:'mkdir',path:'/self/new',expected:null,expectedParent:root},{}),error=>error.code==='EXPLORATION_READ_ONLY');

  const content=await readFile(session.value,'/self/inventory.jsonl');
  assert.equal(content.text,'','被拒绝的写入不能改变 /self 内容');
 }finally{
  try{await session?.release()}catch{}
  await run.close();
 }
});
