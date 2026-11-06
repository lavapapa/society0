import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readRows,renderRows,readFile} from '../.build/core/dist/public/store.js';
import {setup,table} from './b2-support.mjs';

const file='/public/market/prices/2026-11.jsonl';
const text=bytes=>Buffer.from(bytes).toString('utf8');

test('R1写入、R2更新与撤下、R3撤下后各as-of读取正确',async()=>{
 const run=await setup();try{
  const r1=await run.tx('b2-a1',async tx=>{
   await run.ctx.public.writer(tx).upsert(table.path,[
    {id:'a',at:'2026-11-01',price:'1.00',note:'A'},
    {id:'b',at:'2026-11-02',price:'2.00',note:'B'}]);
  });
  const R1=r1.receipt.revision;
  const r2=await run.tx('b2-a2',async tx=>{
   const w=run.ctx.public.writer(tx);
   await w.upsert(table.path,[{id:'a',at:'2026-11-01',price:'3.00',note:'A2'}]);
   await w.retract(table.path,[{id:'b'}]);
   await w.upsert(table.path,[{id:'c',at:'2026-11-03',price:'4.00'}]);
  });
  const R2=r2.receipt.revision;
  const r3=await run.tx('b2-a3',tx=>run.ctx.public.writer(tx).retract(table.path,[{id:'a'}]));
  const R3=r3.receipt.revision;
  assert.ok(BigInt(R1)<BigInt(R2)&&BigInt(R2)<BigInt(R3));
  await run.read(async view=>{
   const read=async R=>readRows(view,file,R,['all'],{maxRows:50,maxBytes:1<<20}).then(rows=>rows.map(row=>JSON.parse(text(row.line))));
   const one=await read(R1);
   assert.deepEqual(one.map(row=>[row.id,row.price]),[['a','1.00'],['b','2.00']]);
   const two=await read(R2);
   assert.deepEqual(two.map(row=>[row.id,row.price]),[['a','3.00'],['c','4.00']]);
   const three=await read(R3);
   assert.deepEqual(three.map(row=>row.id),['c']);
   assert.equal(text(await renderRows(view,file,R1,['all'])),one.map(row=>JSON.stringify(row)+'\n').join(''));
   const aggregate=await readFile(view,file,R2);
   assert.equal(aggregate.rows,2);
   assert.equal(aggregate.bytes,Buffer.byteLength(JSON.stringify(two[0]))+1+Buffer.byteLength(JSON.stringify(two[1]))+1);
   assert.deepEqual(aggregate.audiences,['all']);
  });
 }finally{await run.close()}
});
test('同事务内同一行多次写入只保留最后一次',async()=>{
 const run=await setup();try{
  const result=await run.tx('b2-dup',async tx=>{
   const w=run.ctx.public.writer(tx);
   const first=await w.upsert(table.path,[{id:'a',at:'2026-11-01',note:'first'}]);
   const second=await w.upsert(table.path,[{id:'a',at:'2026-11-01',note:'second'}]);
   return {first,second};
  });
  assert.equal(result.value.first.changed,1);
  assert.equal(result.value.second.changed,1);
  await run.read(async view=>{
   const rows=await readRows(view,file,result.receipt.revision,['all'],{maxRows:10,maxBytes:1<<20});
   assert.equal(rows.length,1);
   assert.equal(JSON.parse(text(rows[0].line)).note,'second');
   assert.equal(JSON.parse(text(rows[0].line)).id,'a');
  });
 }finally{await run.close()}
});
