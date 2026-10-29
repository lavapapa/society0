import {test} from 'node:test';import assert from 'node:assert/strict';import {createServer} from 'node:http';import {once} from 'node:events';
import {fixture,activation,request} from './pr10-support.mjs';
import Drivers from '../../../evidence/pr10/stage/core/dist/drivers/index.js';
import Perceptions,{perceptionMigration} from '../../../evidence/pr10/stage/core/dist/perception/index.js';
import Repository,{measurementMigration} from '../../../evidence/pr10/stage/social/dist/measurement-repository/index.js';
import Measurements from '../../../evidence/pr10/stage/social/dist/measurements/index.js';
import Every from '../../../evidence/pr10/stage/social/dist/measurement-every/index.js';
import Rankings from '../../../evidence/pr10/stage/social/dist/social-ranking/index.js';
import Recommendations from '../../../evidence/pr10/stage/social/dist/social-recommendations/index.js';
import SocialPerception from '../../../evidence/pr10/stage/social/dist/social-perception/index.js';

test('PR10 failed HTTP interview replays settled original failure without resending',async()=>{
 let calls=0;const server=createServer((req,res)=>{calls++;res.statusCode=503;res.end('完整原始失败响应')});server.listen(0,'127.0.0.1');await once(server,'listening');let f,held;
 try{f=await fixture({migrations:[measurementMigration]});const c=f.ctx;for(const plugin of [Drivers,Repository,Measurements,Every])await c.plugin(plugin);
  await c.plugin({inject:['measurements'],apply(ctx){ctx.measurements.register({id:'failed-http',kind:'interview',required:true,async observe(){const response=await fetch('http://127.0.0.1:'+server.address().port);const original={status:response.status,body:await response.text()};throw Object.assign(Error('HTTP_INTERVIEW_'+response.status),{original})}})}});
  held=await c.executionFence.reserve({id:'failed-interview'});const context=activation(f,held,'failed-interview');
  for(let i=0;i<2;i++){const result=await c.measurements.collect(context);assert.equal(result.status,'incomplete');assert.match(result.value[0].failure,/HTTP_INTERVIEW_503/);assert.deepEqual(result.requiredOperations,[])}
  assert.equal(calls,1);const identity={lineage:c.attempts.authority().lineage,operationId:'failed-interview/measurement/failed-http',attempt:0};const probe=await c.attempts.probe(identity);assert.equal(probe.record.status,'settled');assert.equal(probe.record.result.original.body,'完整原始失败响应');assert.equal(probe.record.result.ok,false);
 }finally{await held?.release();await f?.host.close();server.close();await once(server,'close')}
});

test('PR10 native perception shares item budget and commits recommendation prefix across two pages to exhaustion',async()=>{
 const f=await fixture({migrations:[perceptionMigration({package:'@society0/core',entry:'perception'})]});let held,selection;
 try{const c=f.ctx;for(const plugin of [Perceptions,Rankings,Recommendations,SocialPerception])await c.plugin(plugin);held=await c.executionFence.reserve({id:'recommendation-pages'});
  for(const id of ['one','two'])await c.actions.execute('social.publish',{id,content:'完整推荐原文 '+id},activation(f,held,'publish-'+id));
  let view=await c.database.snapshot({maxAgeMs:30000});const thread=await c.threads.open('c','pagination',view);await view.close();
  const context=activation(f,held,'perception-pages','c'),scope={actorId:'c',momentId:'pagination',threadId:thread.id,reason:null,logicalTime:'2'};selection=await c.perceptions.acquire(['social/profile','social/recommendations'],context);
  const found=[];for(let i=0;i<3;i++){
   const batch=await selection.value.collect(scope,{maxItems:2,maxResponseBytes:65536},context);const read=batch.reads.find(x=>x.sourceId==='social/recommendations');
   assert.equal(read.page.hasMore,i===0);assert.equal(read.page.items.length,i===2?0:1);for(const percept of read.page.items){found.push(percept.id);assert.match(percept.material.value.features.content,/完整推荐原文/)}
   const inputs=[];for(const read of batch.reads){if(!read.page.items.length)continue;const message=await c.threads.prepare({id:'page-'+i+'-'+read.sourceId,interactionId:thread.interactionId,role:'user',content:read.page.items,originalProviderFields:{source:read.sourceId}},context.signal);inputs.push({sourceId:read.sourceId,perceptIds:read.page.items.map(x=>x.id),message,contexts:read.page.items.map(x=>x.material)})}
   await c.database.transaction(held.lease,request('consume-page-'+i),tx=>selection.value.commit(tx,batch,inputs));
  }
  assert.equal(new Set(found).size,2);view=await c.database.snapshot({maxAgeMs:30000});try{const rows=await view.query({sql:"SELECT count(*) AS n FROM perception_links WHERE source_id='social/recommendations'"},{maxRows:1,maxBytes:1024});assert.equal(rows[0].n,2n)}finally{await view.close()}
 }finally{await selection?.release();await held?.release();await f.host.close()}
});
