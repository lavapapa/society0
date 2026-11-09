import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture,activation,request} from './pr10-support.mjs';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';

test('L3 fix: recommendation candidates truncate oldest items to page bytes',async()=>{
 const f=await fixture();let held;
 try{
  const c=f.ctx,ids=['p1','p2','p3','p4','p5'];
  held=await c.executionFence.reserve({id:'l3-truncate'});
  await c.database.transaction(held.lease,request('l3-truncate-seed'),async tx=>{
   for(const [index,id] of ids.entries())await c.socialRepository.publish(tx,{id,authorId:'a',content:id+' '+'x'.repeat(200),tags:[],createdTime:String(index+1)},[]);
  });
  await c.plugin(Rankings);
  await c.plugin(Recommendations,{sources:['bulk/posts']});
  await c.plugin({inject:['recommendationSources','socialRepository'],apply(ctx){
   ctx.recommendationSources.register({id:'bulk/posts',version:1,dependencies:['social/content'],versions:view=>ctx.socialRepository.versions(view),
    collect:async(scope,view)=>ctx.socialRepository.posts(view,{maxItems:100,maxResponseBytes:65536},{excludeAuthor:scope.actorId})});
  }});
  const maxBytes=900;
  const prepared=await c.recommendations.prepare({actorId:'b',momentId:'truncate',query:{time:'6'}},{maxItems:10,maxBytes},activation(f,held,'l3-truncate-prepare','b'));
  assert.equal(prepared.status,'ready',JSON.stringify(prepared));
  assert.ok(prepared.input.truncated>0,JSON.stringify(prepared.input));
  assert.ok(Buffer.byteLength(JSON.stringify(prepared.input))<=maxBytes,JSON.stringify({bytes:Buffer.byteLength(JSON.stringify(prepared.input)),maxBytes}));
  assert.ok(prepared.input.items.length>0,JSON.stringify(prepared.input));
  assert.equal(prepared.input.items.length+prepared.input.truncated,ids.length,JSON.stringify(prepared.input));
  assert.deepEqual(prepared.input.items.map(item=>item.id),ids.slice(ids.length-prepared.input.items.length));
 }finally{await held?.release();await f.host.close()}
});
