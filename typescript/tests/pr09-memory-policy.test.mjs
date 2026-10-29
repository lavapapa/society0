import {test} from 'node:test';import assert from 'node:assert/strict';import {setup,request} from './pr09-support.mjs';
import Memory from '../.build/extras/dist/memory/index.js';
import ExplicitExperience from '../.build/extras/dist/memory-write-experience/index.js';
test('PR09-T03 automatic write, recall and active tools switch independently while original Thread persists',async()=>{
 const f=await setup();const scope={actorId:'a',momentId:'m',query:null};
 const query={actorId:'a',maxItems:10,maxResponseBytes:16384};
 try {
  await f.write('original-experience',tx=>f.ctx.memoryRepository.append(tx,{id:'seed-memory',actorId:'a',threadId:f.threadId,body:'original',artifacts:[]}));
  await f.ctx.plugin(ExplicitExperience);
  for(const disabled of ['write','recall','tools']){
   const fiber=await f.ctx.plugin(Memory,{[disabled]:false});
   if(disabled==='write')await assert.rejects(f.write('disabled-write',tx=>f.ctx.memory.write(tx,{id:'disabled',actorId:'a',threadId:f.threadId,body:'not written',artifacts:[]})),/MEMORY_WRITE_DISABLED/);
   else await f.write('enabled-write-'+disabled,tx=>f.ctx.memory.write(tx,{id:disabled,actorId:'a',threadId:f.threadId,body:disabled,artifacts:[]}));
   if(disabled==='recall')await assert.rejects(f.read(view=>f.ctx.memory.recall(query,view)),/MEMORY_RECALL_DISABLED/);
   else assert.ok((await f.read(view=>f.ctx.memory.recall(query,view))).items.length>0);
   const actions=await f.read(view=>f.ctx.actions.discover(scope,view,{maxItems:20,maxResponseBytes:16384}));assert.equal(actions.items.some(action=>action.name==='memory.recall'),disabled!=='tools');
   if(disabled!=='tools'){const reservation=await f.ctx.executionFence.reserve({id:'tool-'+disabled});try{const context={ctx:f.ctx,runId:f.runId,generation:'g',operationId:'tool-'+disabled,signal:new AbortController().signal,actorId:'a',momentId:'m',logicalTime:0,database:f.ctx.database,writeLease:reservation.lease};const called=await f.ctx.actions.execute('memory.recall',{},context);assert.equal(called.status,'completed');assert.ok(called.value.items.length>0);if(disabled==='write'){const written=await f.ctx.actions.execute('memory.write',{threadId:f.threadId,body:'explicit tool despite automatic write off'},{...context,operationId:'tool-write'});assert.equal(written.status,'completed')}}finally{await reservation.release()}}
   const original=await f.read(view=>f.ctx.threads.page(f.threadId,scope,view,{maxItems:10,maxResponseBytes:16384}));assert.equal(original.items.length,1);assert.equal(original.items[0].id,'seed');await fiber.dispose();
  }
  assert.equal((await f.read(view=>f.ctx.memoryRepository.read(view,'seed-memory'))).body,'original');
  await f.ctx.plugin(Memory);await f.write('explicit-experience-message',tx=>f.ctx.threads.append(tx,f.threadId,{id:'lesson',interactionId:f.threadId,role:'experience',content:{lesson:'preserve original'},originalProviderFields:{source:'explicit'}}));
  const thread=await f.read(view=>f.ctx.threads.open('a','m',view));const reservation=await f.ctx.executionFence.reserve({id:'extract-experience'});
  try{const context={ctx:f.ctx,runId:f.runId,generation:'g',operationId:'extract-experience',signal:new AbortController().signal,actorId:'a',momentId:'m',logicalTime:0,database:f.ctx.database,writeLease:reservation.lease};const extracted=await f.ctx.memory.extract(thread,context);assert.equal(extracted.length,1);assert.deepEqual(extracted[0].body,{lesson:'preserve original'});await f.ctx.database.transaction(reservation.lease,request('persist-experience'),tx=>f.ctx.memory.write(tx,extracted[0]))}finally{await reservation.release()}
  assert.equal((await f.read(view=>f.ctx.threads.open('a','m',view))).messageCount,'2');

 }finally{await f.close()}
});
