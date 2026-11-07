import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';

test('1事务upsert1000行（含FTS）热p50<30ms',async()=>{
 const run=await setup();try{
  await run.tx('b5-write-warm',tx=>run.ctx.public.writer(tx).upsert(table.path,Array.from({length:10},(_,i)=>({id:'warm'+i,at:'2026-09-01',price:'1.00',note:'warm'}))));
  const times=[];
  for(let round=0;round<5;round++){
   const at=['2026-10','2026-11','2026-12','2027-01','2027-02'][round]+'-01';
   const rows=Array.from({length:1000},(_,i)=>({id:`w${round}-${i}`,at,price:'1.00',note:'write budget row '+i}));
   await run.tx('b5-write-budget-'+round,async tx=>{
    const started=performance.now();
    const written=await run.ctx.public.writer(tx).upsert(table.path,rows);
    times.push(performance.now()-started);
    assert.equal(written.changed,1000);
   });
  }
  const sorted=[...times].sort((a,b)=>a-b),p50=sorted[Math.floor((sorted.length-1)/2)],max=sorted[sorted.length-1];
  console.log(JSON.stringify({case:'B5-write-budget',rows:1000,p50:Number(p50.toFixed(2)),max:Number(max.toFixed(2)),runs:times.length}));
  assert.ok(p50<30,`upsert 1000 行热 p50 ${p50.toFixed(2)} ms`);
 }finally{await run.close()}
});
