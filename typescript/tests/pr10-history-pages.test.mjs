// cases: PR10-T03
import test from 'node:test';import assert from 'node:assert/strict';import {join,dirname} from 'node:path';
import {fixture,activation,request} from './pr10-support.mjs';
import Perceptions,{perceptionMigration} from '../.build/core/dist/perception/index.js';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';
import SocialPerception from '../.build/social/dist/social-perception/index.js';
const bounds={maxItems:1,maxResponseBytes:65536};
async function pages(query){let cursor,total;const items=[];let count=0;do{const page=await query({...bounds,...(cursor?{cursor}:{})});total??=page.total;assert.equal(page.total,total);assert.equal(page.items.length,1);items.push(...page.items);cursor=page.next;count++}while(cursor);return {items,total,pages:count}}
async function install(f){for(const plugin of [Perceptions,Rankings,Recommendations,SocialPerception])await f.ctx.plugin(plugin)}
async function snapshot(f){const c=f.ctx,view=await c.database.snapshot({maxAgeMs:30000});try{
 const feed=await pages(input=>c.socialRepository.feed('c',view,input));const neighbors=await pages(input=>c.socialRepository.neighbors('a',view,input,'followers'));
 const tables={};for(const [table,order] of [['perception_progress','actor_id,source_id,domain_id'],['perception_material','batch_id,source_id,percept_id'],['perception_links','batch_id,source_id,percept_id'],['threads','id']])tables[table]=await view.query({sql:'SELECT * FROM '+table+' ORDER BY '+order},{maxRows:20,maxBytes:65536});
 const thread=await c.threads.open('c','history-conversation',view);return {feed,neighbors,tables,threadId:thread.id,interactionId:thread.interactionId};
}finally{await view.close()}}
async function consume(f,reservation,label,expected){const c=f.ctx,context=activation(f,reservation,label,'c');const view=await c.database.snapshot({maxAgeMs:30000});let thread;try{thread=await c.threads.open('c','history-conversation',view)}finally{await view.close()}
 const selection=await c.perceptions.acquire(['social/feed'],context);try{const batch=await selection.value.collect({actorId:'c',momentId:'history-conversation',threadId:thread.id,reason:null},bounds,context);assert.equal(batch.reads[0].page.items.length,1);assert.equal(batch.reads[0].page.items[0].material.value.id,expected);
 const message=await c.threads.prepare({id:label+'-message',interactionId:thread.interactionId,role:'user',content:batch.reads[0].page.items,originalProviderFields:{source:'historical-social',untouched:true}},context.signal);
 await c.database.transaction(reservation.lease,request(label),tx=>selection.value.commit(tx,batch,[{sourceId:'social/feed',perceptIds:batch.reads[0].page.items.map(item=>item.id),message,contexts:batch.reads[0].page.items.map(item=>item.material)}]));
}finally{await selection.release()}}
async function publish(f,id,parent,operations){const c=f.ctx;c.history.bindQuiescence(()=>c.tasks.drain());const lease=await c.executionFence.seal();const point=await c.history.publish(lease,{operationId:'publish-'+id,id,parentId:parent,expectedRevision:await c.database.readRevision(),requiredOperations:operations});if(c.executionFence.snapshot().state==='sealed')c.executionFence.resume(lease);return point}
test('PR10-T03 multipage originals neighbors and perception Thread restore at an older complete point',{timeout:600000},async()=>{
 const migration=perceptionMigration({consumer:'public-social-history'});const f=await fixture({migrations:[migration]});let branch,reservation;
 try{await install(f);reservation=await f.ctx.executionFence.reserve({id:'seed-history'});const receipts=[];
 for(let i=0;i<3;i++)receipts.push(await f.ctx.actions.execute('social.publish',{id:'history-post-'+i,content:'完整原始正文😀-'+i,tags:['history','tag-'+i],metadata:{original:{ordinal:i,retained:true}}},{...activation(f,reservation,'history-post-'+i),logicalTime:String(i+1)}));
 await consume(f,reservation,'consume-first','history-post-0');await reservation.release();reservation=undefined;
 const point=await publish(f,'selected-history',f.root.id,[...receipts.map(receipt=>receipt.operationId),'consume-first']);const before=await snapshot(f);
 assert.equal(before.feed.total,'3');assert.equal(before.feed.pages,3);assert.equal(before.neighbors.total,'2');assert.equal(before.neighbors.pages,2);assert.deepEqual(before.feed.items.map(item=>item.metadata.original.ordinal),[0,1,2]);assert.equal(before.tables.perception_material.length,1);assert.equal(before.tables.perception_links.length,1);
 reservation=await f.ctx.executionFence.reserve({id:'advance-source'});const late=await f.ctx.actions.execute('social.publish',{id:'after-selected-point',content:'新点数据不能进入旧点'}, {...activation(f,reservation,'late-post'),logicalTime:'99'});await consume(f,reservation,'consume-second','history-post-1');await reservation.release();reservation=undefined;
 const latest=await publish(f,'latest-history',point.id,[late.operationId,'consume-second']);const advanced=await snapshot(f);assert.equal(advanced.feed.total,'4');assert.equal(advanced.tables.perception_material.length,2);
 const target=join(dirname(f.directory),'pr10-historical-pages');await f.ctx.history.fork({operationId:'historical-pages-fork',pointId:point.id,targetRunId:'historical-pages',targetDirectory:target});
 branch=await fixture({restore:true,directory:target,runId:'historical-pages',attemptDirectory:f.attemptDirectory,migrations:[migration]});await install(branch);const restored=await snapshot(branch);assert.deepEqual(restored,before);assert.equal((await f.ctx.history.current()).id,latest.id);
 reservation=await branch.ctx.executionFence.reserve({id:'continue-restored-cursor'});await consume(branch,reservation,'restored-second','history-post-1');await reservation.release();reservation=undefined;const continued=await snapshot(branch);assert.equal(continued.tables.perception_material.length,2);assert.equal(continued.tables.perception_links.length,2);assert.equal(continued.threadId,before.threadId);assert.equal(continued.feed.total,'3');
 console.log(JSON.stringify({gate:'PR10-T03-public-history',selected:point.id,latest:latest.id,feedPages:before.feed.pages,neighborPages:before.neighbors.pages,restoredMaterials:restored.tables.perception_material.length,continuedMaterials:continued.tables.perception_material.length,target}));
 }finally{await reservation?.release();await branch?.host.close();await f.host.close()}
});
