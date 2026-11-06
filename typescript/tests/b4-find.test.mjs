import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';

const dir='/public/market/prices';
const total=10000;

// B4：1 万个文件 find < 30 ms，结果按 code point 有序。
test('1万文件find下推：结果有序且<30ms',async()=>{
 const run=await setup();try{
  await run.tx('b4-find-files',async tx=>{
   const at=BigInt(tx.baseRevision)+1n;
   const files=[];
   for(let i=0;i<total;i++)files.push([`${dir}/f${String(i).padStart(5,'0')}.jsonl`,dir,at,table.path,1,2,'["all"]']);
   for(let offset=0;offset<files.length;offset+=256)await tx.executeMany('INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences) VALUES(?,?,?,NULL,?,?,?,?)',files.slice(offset,offset+256),{maxRows:256,maxInputBytes:1<<20});
   await tx.execute({sql:'INSERT INTO public_nodes(path,parent,rev_from,rev_to,kind,provider,node_id,media,meta,audience,body,artifact,bytes) VALUES(?,?,?,NULL,?,?,?,?,?,?,?,?,?)',parameters:[dir,'/public/market',at,'dir','public',dir,'application/json','{"title":"价格","description":"日度价格"}','all',null,null,null]});
  });
  await run.openMoment('m');
  const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b4-find'));
  try{
   const started=performance.now();
   const result=await session.value.find({root:'/public',glob:'*.jsonl',page:{maxItems:100,maxResponseBytes:64*1024*1024}});
   const elapsed=performance.now()-started;
   assert.equal(result.items.length,100);
   assert.equal(result.total,String(total));
   const paths=result.items.map(node=>node.path);
   assert.deepEqual(paths,[...paths].sort((a,b)=>a<b?-1:a>b?1:0));
   assert.ok(elapsed<30,`find 耗时 ${elapsed.toFixed(2)} ms 超过 30 ms`);
  }finally{await session.release()}
 }finally{await run.close()}
});
