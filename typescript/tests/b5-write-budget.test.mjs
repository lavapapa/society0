import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';

test('1事务upsert1000行（含FTS）<30ms',async()=>{
 const run=await setup();try{
  await run.tx('b5-write-warm',tx=>run.ctx.public.writer(tx).upsert(table.path,Array.from({length:10},(_,i)=>({id:'warm'+i,at:'2026-10-01',price:'1.00',note:'warm'}))));
  const rows=Array.from({length:1000},(_,i)=>({id:'w'+i,at:'2026-11-01',price:'1.00',note:'write budget row '+i}));
  const result=await run.tx('b5-write-budget',async tx=>{
   const started=performance.now();
   const written=await run.ctx.public.writer(tx).upsert(table.path,rows);
   const ms=performance.now()-started;
   console.log(JSON.stringify({case:'B5-write-budget',rows:1000,ms:Number(ms.toFixed(2))}));
   assert.ok(ms<30,`upsert 1000 行耗时 ${ms.toFixed(2)} ms`);
   return written;
  });
  assert.equal(result.value.changed,1000);
 }finally{await run.close()}
});
