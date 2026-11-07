import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

test('搜索超过exploration.maxSearchBytes时返回边界错误而非静默少给',async()=>{
 const run=await setup({exploration:{maxSearchBytes:32,maxSearchOutputBytes:1024}});try{
  await run.tx('b5-budget-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'budget needle'}]));
  await run.openMoment('m');
  const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-budget'));
  try{
   await assert.rejects(session.value.grep({root:'/public',pattern:'needle',mode:'literal',page:{maxItems:10,maxResponseBytes:1024}}),error=>error.code==='EXPLORATION_SEARCH_BUDGET'||String(error).includes('EXPLORATION_SEARCH_BUDGET'));
  }finally{await session.release()}
 }finally{await run.close()}
});
