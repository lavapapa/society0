import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const readAll=async(content,node)=>{
 const chunks=[];
 for await(const chunk of content.read({offset:'0',length:Number(node.rawBytes),maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
 return Buffer.concat(chunks);
};

// B4：200 并发读取走 B3a reader 池与快照排队，不出现 SQLITE_SNAPSHOT_BUDGET，缓存命中后只渲染一次。
test('200并发读取不出现SQLITE_SNAPSHOT_BUDGET且共享缓存',async()=>{
 const run=await setup({readerWorkers:2,maxSnapshots:3});try{
  await run.tx('b4-load-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  await run.openMoment('m');
  const tasks=Array.from({length:200},(_,i)=>run.ctx.exploration.acquire(run.scope('actor-'+i,'m'),run.acquisition('actor-'+i,'m','b4-load-'+i)).then(async session=>{
   try{
    return await session.value.withView(async current=>{
     const node=await current.stat(file);
     const handle=await current.open(node);
     try{return (await readAll(handle.value,handle.value.node)).length}finally{await handle.release()}
    });
   }finally{await session.release()}
  }));
  const results=await Promise.allSettled(tasks);
  const failures=results.filter(result=>result.status==='rejected').map(result=>String(result.reason));
  assert.deepEqual(failures.filter(reason=>reason.includes('SQLITE_SNAPSHOT_BUDGET')),[]);
  assert.deepEqual(failures,[]);
  const sizes=new Set(results.map(result=>result.value));
  assert.equal(sizes.size,1);
  assert.ok(run.ctx.public.diagnostics().cache.renders<=1,'并发命中同一 etag 只应渲染一次');
 }finally{await run.close()}
});
