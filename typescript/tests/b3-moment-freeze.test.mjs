import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b2-support.mjs';

const file='/public/actors/actors.jsonl';
const rowsAsOf=async(view,R)=>view.query({sql:'SELECT line FROM public_rows WHERE file=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) ORDER BY row_key',parameters:[file,BigInt(R),BigInt(R)]},{maxRows:100,maxBytes:1<<20}).then(rows=>rows.map(row=>JSON.parse(Buffer.from(row.line).toString('utf8')).id));

test('moment打开后提交写，m内as-of不变，m+1可见',async()=>{
 const run=await setup({momentViews:'clock'});try{
  const frozen=await run.tx('b3-open-m1',tx=>run.ctx.momentViews.open(tx,'m1'));
  const R1=frozen.value;
  const write=await run.tx('b3-write-a',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  assert.ok(BigInt(write.receipt.revision)>BigInt(R1));
  const m1=await run.ctx.momentViews.acquire({momentId:'m1'},run.activation('m1'));
  try{
   assert.equal(m1.value.revision,R1);
   assert.equal(m1.value.frozen,true);
   assert.deepEqual(await rowsAsOf(m1.value,R1),[]);
   assert.ok((await m1.value.pinned()).revision>=R1);
  }finally{await m1.release()}
  const m2open=await run.tx('b3-open-m2',tx=>run.ctx.momentViews.open(tx,'m2'));
  const m2=await run.ctx.momentViews.acquire({momentId:'m2'},run.activation('m2'));
  try{
   assert.equal(m2.value.revision,m2open.value);
   assert.deepEqual(await rowsAsOf(m2.value,m2open.value),['a']);
  }finally{await m2.release()}
  const again=await run.tx('b3-open-m1-again',tx=>run.ctx.momentViews.open(tx,'m1'));
  assert.equal(again.value,R1);
 }finally{await run.close()}
});
