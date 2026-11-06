import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b2-support.mjs';

const file='/public/actors/actors.jsonl';
const ids=async(view,sql,parameters)=>view.query({sql,parameters},{maxRows:100,maxBytes:1<<20}).then(rows=>rows.map(row=>JSON.parse(Buffer.from(row.line).toString('utf8')).id));

test('冻结视图用R_m，self实时看到本moment已提交写入',async()=>{
 const run=await setup({momentViews:'clock'});try{
  await run.tx('b3-sv-open',tx=>run.ctx.momentViews.open(tx,'m'));
  await run.tx('b3-sv-write',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  const model=await run.ctx.momentViews.read({momentId:'m'},run.activation('m'));
  try{
   assert.deepEqual(await ids(model.value.frozen,`SELECT line FROM public_rows WHERE file=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) ORDER BY row_key`,[file,BigInt(model.value.frozen.revision),BigInt(model.value.frozen.revision)]),[]);
   const self=await model.value.self();
   assert.deepEqual(await ids(self,'SELECT line FROM public_rows WHERE file=? AND rev_to IS NULL ORDER BY row_key',[file]),['a']);
   await run.tx('b3-sv-write-2',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'b',role:'all',name:'B'}]));
   const later=await model.value.self();
   assert.deepEqual(await ids(later,'SELECT line FROM public_rows WHERE file=? AND rev_to IS NULL ORDER BY row_key',[file]),['a','b']);
   assert.deepEqual(await ids(model.value.frozen,`SELECT line FROM public_rows WHERE file=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) ORDER BY row_key`,[file,BigInt(model.value.frozen.revision),BigInt(model.value.frozen.revision)]),[]);
  }finally{await model.release()}
 }finally{await run.close()}
});
