import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const readAll=async(content,node)=>{
 const chunks=[];
 for await(const chunk of content.read({offset:'0',length:Number(node.rawBytes),maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
 return Buffer.concat(chunks);
};

// B4：1000 个 actor 读同一个只含 all 的文件，渲染次数 = 1（其余命中缓存）。
test('1000个actor共享只含all的文件：渲染计数=1',async()=>{
 const run=await setup();try{
  await run.tx('b4-share-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'},{id:'b',role:'all',name:'B'}]));
  await run.openMoment('m');
  const mount=run.ctx.public.mount;
  assert.ok(mount,'public mount must be registered');
  const before=run.ctx.public.diagnostics().cache.renders;
  let expected;
  await run.read(async view=>{
   for(let i=0;i<1000;i++){
    const scope={actorId:'actor-'+i,momentId:'m',query:null};
    const node=await mount.stat(file,scope,view);
    const handle=await mount.open(node,scope,view);
    const bytes=await readAll(handle.value,handle.value.node);
    await handle.release();
    expected??=Buffer.from(bytes).toString('utf8');
    assert.equal(Buffer.from(bytes).toString('utf8'),expected);
   }
  });
  const after=run.ctx.public.diagnostics().cache.renders;
  assert.equal(after-before,1,'同一内容只应渲染一次');
  console.log(JSON.stringify({case:'B4-share',actors:1000,renders:after-before,memoryHits:run.ctx.public.diagnostics().cache.memoryHits}));
  assert.equal(expected,'{"id":"a","role":"all","name":"A"}\n{"id":"b","role":"all","name":"B"}\n');
 }finally{await run.close()}
});
