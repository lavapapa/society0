import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';

test('B9 public writer按数据库maxQueryBytes生效，超限body报PUBLIC_BODY_BUDGET',async()=>{
 const limit=262144;
 const run=await setup({tables:[table],maxQueryBytes:limit});
 try{
  const body=Buffer.alloc(limit-8192,97);
  await run.tx('b9-max-ok',tx=>run.ctx.public.writer(tx).publish('/public/notices/ok.md',body));
  const [stored]=await run.read(view=>view.query({
   sql:'SELECT body FROM public_nodes WHERE path=? AND rev_to IS NULL',parameters:['/public/notices/ok.md']
  },{maxRows:1,maxBytes:limit}));
  assert.equal(Buffer.from(stored.body).byteLength,body.byteLength,'小于maxQueryBytes的body应完整写入，不截断');

  await assert.rejects(()=>run.tx('b9-max-big',tx=>run.ctx.public.writer(tx).publish('/public/notices/big.md',Buffer.alloc(limit+1,98))),error=>error.code==='PUBLIC_BODY_BUDGET','超过maxQueryBytes的body必须明确拒绝');
  const [big]=await run.read(view=>view.query({
   sql:'SELECT count(*) AS n FROM public_nodes WHERE path=?',parameters:['/public/notices/big.md']
  },{maxRows:1,maxBytes:1024}));
  assert.equal(Number(big.n),0,'拒绝后不能留下文档节点');
 }finally{await run.close()}
});
