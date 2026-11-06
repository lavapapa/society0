import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b2-support.mjs';

const file='/public/actors/actors.jsonl';
const rowsAsOf=async(view,R)=>view.query({sql:'SELECT line FROM public_rows WHERE file=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) ORDER BY row_key',parameters:[file,BigInt(R),BigInt(R)]},{maxRows:100,maxBytes:1<<20}).then(rows=>rows.map(row=>JSON.parse(Buffer.from(row.line).toString('utf8')).id));

test('R_m持久化：释放租约、继续写入后重读同一R_m与同一世界',async()=>{
 const run=await setup({momentViews:'clock'});try{
  await run.tx('b3-r-a',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  await run.tx('b3-r-open',tx=>run.ctx.momentViews.open(tx,'m'));
  const first=await run.ctx.momentViews.acquire({momentId:'m'},run.activation('m'));
  const R=first.value.revision;
  assert.deepEqual(await rowsAsOf(first.value,R),['a']);
  await first.release();
  await run.tx('b3-r-b',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'b',role:'all',name:'B'}]));
  await run.read(async view=>assert.equal(await run.ctx.momentViews.revision('m',view),R));
  const second=await run.ctx.momentViews.acquire({momentId:'m'},run.activation('m'));
  try{
   assert.equal(second.value.revision,R);
   assert.deepEqual(await rowsAsOf(second.value,R),['a']);
  }finally{await second.release()}
 }finally{await run.close()}
});
test('first-acquire记录R_m并在后续读取保持不变',async()=>{
 const run=await setup({momentViews:'first-acquire'});try{
  const first=await run.ctx.momentViews.acquire({momentId:'fresh'},run.activation('fresh'));
  const R=first.value.revision;
  assert.equal(first.value.frozen,true);
  await first.release();
  await run.tx('b3-fa-write',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'z',role:'all',name:'Z'}]));
  const second=await run.ctx.momentViews.acquire({momentId:'fresh'},run.activation('fresh'));
  try{assert.equal(second.value.revision,R)}finally{await second.release()}
  await assert.rejects(run.ctx.momentViews.acquire({momentId:'unknown'},{runId:'b2',generation:'g',operationId:'x',signal:run.reservation.signal}),/MOMENT_VIEW_OPEN/);
 }finally{await run.close()}
});
test('live模式每次取当前revision且frozen为false',async()=>{
 const run=await setup({momentViews:'live'});try{
  const first=await run.ctx.momentViews.acquire({momentId:'live'},run.activation('live'));
  assert.equal(first.value.frozen,false);
  const R1=first.value.revision;
  await first.release();
  await run.tx('b3-live-write',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'q',role:'all',name:'Q'}]));
  const second=await run.ctx.momentViews.acquire({momentId:'live'},run.activation('live'));
  try{assert.ok(BigInt(second.value.revision)>BigInt(R1))}finally{await second.release()}
 }finally{await run.close()}
});
