import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const audienceProvider={id:'b5-audience',async audiences(scope){return scope.actorId==='supplier'?['all','role:supplier']:['all'];}};
const page={maxItems:100,maxResponseBytes:1<<20};

test('候选不泄漏不可见行：命中内容来自受众过滤后的渲染文件',async()=>{
 const run=await setup({audiences:audienceProvider});try{
  await run.tx('b5-audience-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[
   {id:'public',role:'all',name:'公开商品'},
   {id:'secret',role:'role:supplier',name:'dragonfruit 供应商机密'}
  ],{audience:row=>row.role}));
  await run.openMoment('m');
  const search=async actor=>{
   const session=await run.ctx.exploration.acquire(run.scope(actor,'m'),run.acquisition(actor,'m','b5-audience-'+actor));
   try{return (await session.value.grep({root:'/public',pattern:'dragonfruit',mode:'literal',page})).items}finally{await session.release()}
  };
  assert.equal((await search('outsider')).length,0,'不可见行不得通过 FTS 候选泄漏');
  const visible=await search('supplier');
  assert.equal(visible.length,1);
  assert.equal(visible[0].path,file);
  assert.match(visible[0].lineText,/dragonfruit/);
 }finally{await run.close()}
});
