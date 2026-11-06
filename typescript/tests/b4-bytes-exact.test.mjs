import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const audienceProvider={id:'b4-roles-bytes',async audiences(){return ['all','role:supplier'];}};

// B4：stat.rawBytes 等于按受众过滤后实际读取的字节数。
test('rawBytes等于受众过滤后的实际读取字节数',async()=>{
 const run=await setup({audiences:audienceProvider});try{
  await run.tx('b4-bytes-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[
   {id:'a',role:'role:supplier',name:'甲'},
   {id:'b',role:'role:buyer',name:'乙'},
   {id:'c',role:'all',name:'丙'}],{audience:row=>row.role}));
  await run.openMoment('m');
  const mount=run.ctx.public.mount;
  await run.read(async view=>{
   const scope={actorId:'supplier',momentId:'m',query:null};
   const node=await mount.stat(file,scope,view);
   const handle=await mount.open(node,scope,view);
   const chunks=[];
   for await(const chunk of handle.value.read({offset:'0',length:Number(node.rawBytes),maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
   await handle.release();
   const bytes=Buffer.concat(chunks);
   assert.equal(Number(node.rawBytes),bytes.length);
   const lines=bytes.toString('utf8').trim().split('\n').map(line=>JSON.parse(line).id);
   assert.deepEqual(lines,['c','a']);
  });
 }finally{await run.close()}
});
