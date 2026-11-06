import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const audienceProvider={id:'b4-roles',async audiences(scope){return scope.actorId==='supplier'?['all','role:supplier']:scope.actorId==='buyer'?['all','role:buyer']:['all'];}};
const text=bytes=>Buffer.from(bytes).toString('utf8');

// B4：两个受众看到不同行，第三个受众看不到该文件（不可见，而不是空文件）。
test('受众求交：两个角色看到不同行，第三个受众不可见',async()=>{
 const run=await setup({audiences:audienceProvider});try{
  await run.tx('b4-asym-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,[
   {id:'s',role:'role:supplier',name:'供应商'},
   {id:'b',role:'role:buyer',name:'采购商'}],{audience:row=>row.role}));
  await run.openMoment('m');
  const mount=run.ctx.public.mount;
  await run.read(async view=>{
   const read=async actor=>{
    const scope={actorId:actor,momentId:'m',query:null};
    const node=await mount.stat(file,scope,view);
    const handle=await mount.open(node,scope,view);
    const chunks=[];
    for await(const chunk of handle.value.read({offset:'0',length:Number(node.rawBytes),maxBytes:32*1024*1024},new AbortController().signal))chunks.push(Buffer.from(chunk));
    await handle.release();
    return text(Buffer.concat(chunks));
   };
   assert.equal(await read('supplier'),'{"id":"s","role":"role:supplier","name":"供应商"}\n');
   assert.equal(await read('buyer'),'{"id":"b","role":"role:buyer","name":"采购商"}\n');
   const outsider={actorId:'outsider',momentId:'m',query:null};
   assert.equal(await mount.visible(file,outsider,view),false,'无交集受众的文件必须不可见');
   const listed=await mount.list('/public/actors',outsider,view,{maxItems:50,maxResponseBytes:1<<20});
   assert.equal(listed.items.some(node=>node.path===file),false);
   await assert.rejects(mount.stat(file,outsider,view),error=>error.code==='EXPLORATION_NOT_VISIBLE'||String(error).includes('EXPLORATION_NOT_VISIBLE'));
  });
 }finally{await run.close()}
});
