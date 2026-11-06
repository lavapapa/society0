import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const readAll=async(content,node)=>{
 const chunks=[];
 for await(const chunk of content.read({offset:'0',length:Number(node.rawBytes),maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
 return Buffer.concat(chunks);
};

// B4：关闭 run 后重新 Database.open，读到同一 R_m 与同一 /public 视图。
test('真实reopen：同一R_m与同一/public视图',async()=>{
 const first=await setup();let directory,R,bytes;try{
  directory=first.directory;
  await first.tx('b4-reopen-seed',tx=>first.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  R=(await first.openMoment('m')).value;
  const session=await first.ctx.exploration.acquire(first.scope('a','m'),first.acquisition('a','m','b4-reopen'));
  try{
   const node=await session.value.stat(file);
   const handle=await session.value.open(node);
   try{bytes=await readAll(handle.value,handle.value.node)}finally{await handle.release()}
  }finally{await session.release()}
 }finally{await first.close()}
 const second=await setup({directory,migrate:false});try{
  const persisted=await second.read(view=>second.ctx.momentViews.revision('m',view));
  assert.equal(persisted,R);
  const session=await second.ctx.exploration.acquire(second.scope('a','m'),second.acquisition('a','m','b4-reopen-2'));
  try{
   const node=await session.value.stat(file);
   assert.equal(node.versions.find(v=>v.owner==='moment').version,R);
   const handle=await session.value.open(node);
   try{assert.deepEqual(Buffer.from(await readAll(handle.value,handle.value.node)),Buffer.from(bytes))}finally{await handle.release()}
  }finally{await session.release()}
 }finally{await second.close()}
});
