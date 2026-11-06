import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';

// B4：跨 moment open 旧版本节点返回 EXPLORATION_STALE。
test('跨moment打开旧etag返回EXPLORATION_STALE',async()=>{
 const run=await setup();try{
  await run.tx('b4-stale-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  await run.openMoment('m1','m1');
  const first=await run.ctx.exploration.acquire(run.scope('a','m1'),run.acquisition('a','m1','b4-stale-m1'));
  let old;
  try{old=await first.value.stat(file)}finally{await first.release()}
  await run.tx('b4-stale-write',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'b',role:'all',name:'B'}]));
  await run.openMoment('m2','m2');
  const second=await run.ctx.exploration.acquire(run.scope('a','m2'),run.acquisition('a','m2','b4-stale-m2'));
  try{
   const fresh=await second.value.stat(file);
   assert.notEqual(fresh.versions.find(v=>v.owner==='moment').version,old.versions.find(v=>v.owner==='moment').version);
   await assert.rejects(second.value.open(old),error=>error.code==='EXPLORATION_STALE'||String(error).includes('EXPLORATION_STALE'));
  }finally{await second.release()}
 }finally{await run.close()}
});
