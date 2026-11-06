import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readRows} from '../.build/core/dist/public/store.js';
import {setup,table} from './b2-support.mjs';

const domainMigration={owner:'b2-domain',from:0,to:1,after:[],source:{consumer:'b2'},statements:[{sql:'CREATE TABLE b2_inventory(id TEXT PRIMARY KEY NOT NULL,quantity INTEGER NOT NULL)'}]};
const file='/public/market/prices/2026-11.jsonl';

test('领域写与public写同事务提交，回滚后都不可见',async()=>{
 const run=await setup({extra:[domainMigration]});try{
  const committed=await run.tx('b2-atomic-ok',async tx=>{
   await run.ctx.public.writer(tx).upsert(table.path,[{id:'a',at:'2026-11-01',note:'ok'}]);
   await tx.execute({sql:'INSERT INTO b2_inventory VALUES(?,?)',parameters:['a',1]});
  });
  await run.read(async view=>{
   const rows=await readRows(view,file,committed.receipt.revision,['all'],{maxRows:100,maxBytes:1<<20});
   assert.equal(rows.length,1);
   const [domain]=await view.query({sql:'SELECT count(*) AS n FROM b2_inventory'},{maxRows:1,maxBytes:1024});
   assert.equal(Number(domain.n),1);
  });
  await assert.rejects(run.tx('b2-atomic-bad',async tx=>{
   await run.ctx.public.writer(tx).upsert(table.path,[{id:'b',at:'2026-11-02',note:'rolled back'}]);
   await tx.execute({sql:'INSERT INTO b2_inventory VALUES(?,?)',parameters:['b',2]});
   throw new Error('domain failure');
  }),/domain failure/);
  await run.read(async view=>{
   const rows=await readRows(view,file,committed.receipt.revision,['all'],{maxRows:100,maxBytes:1<<20});
   assert.ok(!rows.map(row=>Buffer.from(row.line).toString('utf8')).join('\n').includes('rolled back'));
   const [domain]=await view.query({sql:'SELECT count(*) AS n FROM b2_inventory'},{maxRows:1,maxBytes:1024});
   assert.equal(Number(domain.n),1);
  });
 }finally{await run.close()}
});
