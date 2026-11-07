import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const page={maxItems:100,maxResponseBytes:1<<20};

test('FTS不进入changeset：清空后可测重建',async()=>{
 const run=await setup();try{
  await run.tx('b5-rebuild-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'rebuild needle'}]));
  await run.openMoment('m');
  const search=async()=>{
   const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-rebuild'));
   try{return (await session.value.grep({root:'/public',pattern:'needle',mode:'literal',page})).items}finally{await session.release()}
  };
  assert.equal((await search()).length,1);
  await run.tx('b5-rebuild-clear',tx=>tx.execute({sql:"INSERT INTO public_fts(public_fts) VALUES('delete-all')"}));
  assert.equal((await search()).length,0,'索引缺失时不会静默读到旧候选');
  const rebuild=await run.tx('b5-rebuild-all',async tx=>{
   const started=performance.now();
   await run.ctx.public.rebuildSearch(tx);
   const ms=performance.now()-started;
   console.log(JSON.stringify({case:'B5-fts-rebuild',rows:1,ms:Number(ms.toFixed(2))}));
   return ms;
  });
  assert.ok(rebuild.value>=0);
  assert.equal((await search()).length,1);
 }finally{await run.close()}
});
