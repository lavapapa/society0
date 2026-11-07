import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const page={maxItems:100,maxResponseBytes:1<<20};

test('索引落后/为空时退化为全量受众过滤rg，结果与索引就绪一致',async()=>{
 const run=await setup();try{
  await run.tx('b5-stale-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'stale needle'},{id:'b',role:'all',name:'other'}]));
  await run.openMoment('m');
  const search=async()=>{
   const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-stale'));
   try{return (await session.value.grep({root:'/public',pattern:'needle',mode:'literal',page})).items}finally{await session.release()}
  };
  const fresh=await search();
  assert.equal(fresh.length,1);
  await run.tx('b5-stale-state',tx=>tx.execute({sql:"INSERT INTO public_search_state(public_search_state) VALUES('delete-all')"}));
  assert.deepEqual(await search(),fresh,'索引版本落后时结果必须一致');
  await run.tx('b5-stale-fts',tx=>tx.execute({sql:"INSERT INTO public_fts(public_fts) VALUES('delete-all')"}));
  assert.deepEqual(await search(),fresh,'索引为空时结果必须一致');
  await run.tx('b5-stale-rebuild',tx=>run.ctx.public.rebuildSearch(tx));
  assert.deepEqual(await search(),fresh);
 }finally{await run.close()}
});
