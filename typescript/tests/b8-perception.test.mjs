import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single,page} from './b8-support.mjs';

const file='/public/actors/actors.jsonl';
const pageBounds={maxItems:20,maxResponseBytes:1<<20};

async function activate(run,{actor='a',moment,ids=['public.changes']}={}){
 const thread=await run.read(view=>run.ctx.threads.open(actor,moment,view));
 const context=run.context('b8-perception-'+moment,actor,moment);
 const lease=await run.ctx.perceptions.acquire(ids,context);
 try{
  const batch=await lease.value.collect({actorId:actor,momentId:moment,threadId:thread.id,reason:{}},pageBounds,context);
  const inputs=[];
  for(const read of batch.reads){
   const message=await run.ctx.threads.prepare({id:`${read.sourceId}/${batch.id}`,interactionId:thread.interactionId,role:'user',content:read.page.items,originalProviderFields:{source:read.sourceId}},run.reservation.signal);
   inputs.push({sourceId:read.sourceId,perceptIds:read.page.items.map(item=>item.id),message,contexts:read.page.items.map(item=>item.material)});
  }
  return {thread,lease,batch,inputs};
 }catch(error){await lease.release();throw error}
}
const commit=(run,batch)=>run.runTx('b8-perception-commit-'+batch.batch.id,tx=>batch.lease.value.commit(tx,batch.batch,batch.inputs));
const progress=run=>run.read(async view=>Number((await view.query({sql:'SELECT count(*) AS n FROM perception_progress'},{maxRows:1,maxBytes:1024}))[0].n));
const cursor=run=>run.read(async view=>{const [row]=await view.query({sql:"SELECT cursor FROM perception_progress WHERE actor_id='a' AND source_id='public.changes'",parameters:[]},{maxRows:1,maxBytes:1024});return row?JSON.parse(String(row.cursor)):null});

// B8：连续两次激活只送达新增区间；摘要只含路径与计数。
test('连续激活只送达新增变更，材料只含路径与计数',async()=>{
 const run=await setup({tables:[single],perceptions:true});try{
  await run.runTx('b8-perception-r1',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'},{id:'b',role:'all',name:'B'}]));
  const r1=await run.revision();
  await run.openMoment('m1');

  const first=await activate(run,{moment:'m1'});
  assert.equal(first.batch.reads.length,1);
  assert.equal(first.batch.reads[0].sourceId,'public.changes');
  assert.equal(first.batch.reads[0].page.items.length,1);
  const firstMaterial=first.batch.reads[0].page.items[0].material.value;
  assert.equal(firstMaterial.changes.length,1);
  assert.deepEqual({path:firstMaterial.changes[0].path,kind:firstMaterial.changes[0].kind,rows:firstMaterial.changes[0].rows},{path:file,kind:'added',rows:{added:2,updated:0,retracted:0}});
  assert.equal(JSON.stringify(firstMaterial).includes('"name"'),false,'摘要不得内联行内容');
  await commit(run,first);
  await first.lease.release();
  assert.deepEqual(await cursor(run),String(r1));

  await run.runTx('b8-perception-r2',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A2'}]));
  const r2=await run.revision();
  await run.openMoment('m2');
  const second=await activate(run,{moment:'m2'});
  const secondMaterial=second.batch.reads[0].page.items[0].material.value;
  assert.deepEqual(secondMaterial.changes.map(item=>({path:item.path,kind:item.kind,rows:item.rows})),[{path:file,kind:'updated',rows:{added:0,updated:1,retracted:0}}],'第二次激活只报告新增区间');
  await commit(run,second);
  await second.lease.release();
  assert.deepEqual(await cursor(run),String(r2));

  await run.openMoment('m3');
  const third=await activate(run,{moment:'m3'});
  assert.deepEqual(third.batch.reads[0].page.items,[],'没有新变更时不产生感知材料');
  await commit(run,third);
  await third.lease.release();
 }finally{await run.close()}
});

// B8：提交失败时事务回滚，感知游标不得推进。
test('感知提交失败不推进游标，重试仍从旧游标送达',async()=>{
 const run=await setup({tables:[single],perceptions:true});try{
  await run.runTx('b8-fail-r1',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'},{id:'b',role:'all',name:'B'}]));
  await run.openMoment('m1');
  let fail=true;
  const dispose=run.ctx.perceptions.register({id:'b8-failing',schema:{},cursorScope:'actor',available:async()=>true,versions:async(scope,reader)=>{if(fail&&reader.baseRevision!==undefined)throw Error('b8 failing source');return [{owner:'b8-failing',version:'1'}]},collect:async()=>({items:[],nextCursor:1,through:1,hasMore:false})});
  try{
   const first=await activate(run,{moment:'m1',ids:['public.changes','b8-failing']});
   assert.equal(first.batch.reads[0].page.items.length,1);
   await assert.rejects(commit(run,first),/b8 failing source/);
   await first.lease.release();
   assert.equal(await progress(run),0,'失败后不得写入感知游标');
   const empty=await run.read(view=>run.ctx.threads.page(first.thread.id,{actorId:'a',momentId:'m1',query:null},view,page));
   assert.equal(Number(empty.total),0,'失败后不得留下消息');

   fail=false;
   const retry=await activate(run,{moment:'m1',ids:['public.changes','b8-failing']});
   assert.equal(retry.batch.reads[0].page.items.length,1,'重试仍从旧游标送达同一批变更');
   await commit(run,retry);
   await retry.lease.release();
   assert.equal(await progress(run),2);
   assert.deepEqual(await cursor(run),'1');
   const delivered=await run.read(view=>run.ctx.threads.page(retry.thread.id,{actorId:'a',momentId:'m1',query:null},view,page));
   assert.equal(Number(delivered.total),2,'两个来源各写入一条输入；public.changes 只送达一次');
  }finally{await dispose()}
 }finally{await run.close()}
});
