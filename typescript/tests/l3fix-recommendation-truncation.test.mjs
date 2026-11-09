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

test('L3 fix: truncation budget counts the final input including truncated',async()=>{
 const f=await fixture();let held;
 try{
  const c=f.ctx,ids=['q1','q2','q3'];
  held=await c.executionFence.reserve({id:'l3-final-truncate'});
  await c.database.transaction(held.lease,request('l3-final-truncate-seed'),async tx=>{
   for(const [index,id] of ids.entries())await c.socialRepository.publish(tx,{id,authorId:'a',content:id+' '+'y'.repeat(200),tags:[],createdTime:String(index+1)},[]);
  });
  await c.plugin(Rankings);
  await c.plugin(Recommendations,{sources:['bulk/posts']});
  await c.plugin({inject:['recommendationSources','socialRepository'],apply(ctx){
   ctx.recommendationSources.register({id:'bulk/posts',version:1,dependencies:['social/content'],versions:view=>ctx.socialRepository.versions(view),
    collect:async(scope,view)=>ctx.socialRepository.posts(view,{maxItems:100,maxResponseBytes:65536},{excludeAuthor:scope.actorId})});
  }});
  const scope={actorId:'b',momentId:'final-truncate',query:{time:'4'}};
  const full=await c.recommendations.prepare(scope,{maxItems:10,maxBytes:65536},activation(f,held,'l3-final-truncate-full','b'));
  assert.equal(full.status,'ready',JSON.stringify(full));
  assert.equal(full.input.truncated,undefined,JSON.stringify(full.input));
  const {items,sourcePages,maxBytes:ignored,...rest}=full.input;
  // 让“截断一个条目后不含 truncated 的估算”刚好 ≤ maxBytes，而含 truncated 的最终对象 > maxBytes：
  // 旧实现会停在这里并在 final 校验报错，新实现会再丢一个条目后成功。
  const finalBytes=(keep,max)=>Buffer.byteLength(JSON.stringify({...rest,items:items.slice(items.length-keep),sourcePages,truncated:items.length-keep,maxBytes:max}));
  let boundary=finalBytes(items.length-1,ignored);
  for(let i=0;i<4;i++)boundary=finalBytes(items.length-1,boundary);
  const maxBytes=boundary-1;
  const prepared=await c.recommendations.prepare(scope,{maxItems:10,maxBytes},activation(f,held,'l3-final-truncate-boundary','b'));
  assert.equal(prepared.status,'ready',JSON.stringify(prepared));
  assert.ok(prepared.input.truncated>1,JSON.stringify(prepared.input));
  assert.equal(prepared.input.items.length+prepared.input.truncated,ids.length,JSON.stringify(prepared.input));
  assert.ok(Buffer.byteLength(JSON.stringify(prepared.input))<=maxBytes,JSON.stringify({bytes:Buffer.byteLength(JSON.stringify(prepared.input)),maxBytes}));
 }finally{await held?.release();await f.host.close()}
});
