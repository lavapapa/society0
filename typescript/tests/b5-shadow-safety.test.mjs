import {test} from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {readFile} from 'node:fs/promises';
import {setup,single} from './b4-support.mjs';
import {openConnection} from '../.build/sqlite-native/src/index.mjs';

const migration={owner:'b5-shadow-note',from:0,to:1,after:[],source:{version:1},statements:[{sql:'CREATE TABLE public_fts_note(id INTEGER PRIMARY KEY,v TEXT NOT NULL)'}]};

test('普通表public_fts_note不被影子表过滤排除，changeset与apply保留',async()=>{
 const source=await setup({extra:[migration]});let target,conn;try{
  const result=await source.tx('b5-shadow-write',async tx=>{
   await source.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'shadow'}]);
   await tx.execute({sql:'INSERT INTO public_fts_note(id,v) VALUES(1,?)',parameters:['kept']});
  });
  const segment=join(source.directory,'segments',result.receipt.revision+'.changeset');
  const bytes=await readFile(segment);
  assert.ok(bytes.includes(Buffer.from('public_fts_note')),'普通表必须进入 changeset');
  target=await setup({extra:[migration]});
  conn=openConnection({path:join(target.directory,'world.sqlite'),synchronous:'normal'});
  conn.applySegment(segment);
  assert.equal(conn.query('SELECT v FROM public_fts_note WHERE id=1',[],{maxRows:1,maxBytes:1024})[0].v,'kept');
 }finally{conn?.close();await target?.close();await source.close()}
});
