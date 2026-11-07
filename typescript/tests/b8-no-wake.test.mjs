import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b8-support.mjs';

// B8：/public 变更只进入感知材料，不自行产生激活意图；是否唤醒留给 C 的策略。
test('只有 /public 变更时不产生激活意图',async()=>{
 const decisions=[];
 const run=await setup({tables:[single],perceptions:true,activation:true,wakePolicy:{decide(signal){decisions.push(structuredClone(signal));return 'wake'}}});try{
  await run.runTx('b8-no-wake-r1',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'}]));
  const r1=await run.revision();
  await run.openMoment('m1');

  const pageResult=await run.ctx.public.changes({actorId:'a',momentId:'m1',query:null},'0',String(r1),{maxItems:20,maxResponseBytes:1<<20});
  assert.equal(pageResult.items.length,1);

  const thread=await run.read(view=>run.ctx.threads.open('a','m1',view));
  const context=run.context('b8-no-wake-perception','a','m1');
  const lease=await run.ctx.perceptions.acquire(['public.changes'],context);
  try{
   const batch=await lease.value.collect({actorId:'a',momentId:'m1',threadId:thread.id,reason:{}},{maxItems:20,maxResponseBytes:1<<20},context);
   const read=batch.reads[0];
   assert.equal(read.page.items.length,1);
   assert.equal(read.page.items[0].provenance.notify,'digest','notify 位随材料透传');
   const inputs=[];
   for(const item of read.page.items){
    const message=await run.ctx.threads.prepare({id:`public.changes/${batch.id}`,interactionId:thread.interactionId,role:'user',content:read.page.items,originalProviderFields:{source:read.sourceId}},run.reservation.signal);
    inputs.push({sourceId:read.sourceId,perceptIds:[item.id],message,contexts:[item.material]});
   }
   await run.runTx('b8-no-wake-commit',tx=>lease.value.commit(tx,batch,inputs));
  }finally{await lease.release()}

  assert.deepEqual(decisions,[],'B8 不应调用激活策略或提交唤醒');
  assert.equal(run.ctx.activation.inboxCount('m1'),0,'/public 变更不进入唤醒收件箱');
 }finally{await run.close()}
});
