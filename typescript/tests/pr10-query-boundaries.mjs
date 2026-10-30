import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture,activation,request,bounds} from './pr10-support.mjs';
import Measurements,{measurementMigration} from '../.build/social/dist/measurement-repository/index.js';

test('PR10 public query families preserve original records across filters revisions and byte boundaries',async()=>{
 const f=await fixture({migrations:[measurementMigration]});let held;
 try{
  const c=f.ctx;await c.plugin(Measurements);held=await c.executionFence.reserve({id:'queries'});
  let serial=0;const action=(name,input,actor='a')=>c.actions.execute('social.'+name,input,activation(f,held,'query-action-'+serial++,actor));
  const tx=work=>c.database.transaction(held.lease,request('query-tx-'+serial++),work);
  const read=async work=>{const view=await c.database.snapshot({maxAgeMs:30000});try{return await work(view)}finally{await view.close()}};
  await action('publish',{id:'p1',content:'完整帖子一',tags:['science']});
  await action('publish',{id:'p2',content:'完整帖子二',tags:['other']},'b');
  await action('reply',{id:'r1',postId:'p1',content:'完整原回复'},'b');
  await action('react',{postId:'p1',kind:'like'},'c');
  await action('react',{postId:'p1',kind:'like',remove:true},'c');
  await action('react',{postId:'p1',kind:'like',remove:true},'c');
  await action('react',{postId:'p1',kind:'bookmark'});
  await action('react',{postId:'p1',kind:'bookmark',remove:true});
  await assert.rejects(action('react',{postId:'missing',kind:'like'}),/SOCIAL_POST_MISSING/);
  for(const [id,text] of [['missing','x'],['p1',' '],['p1','x'.repeat(65537)]])
   await assert.rejects(tx(t=>c.socialRepository.revise(t,id,text,[])),/SOCIAL_POST_MISSING|SOCIAL_CONTENT_BUDGET/);
  await tx(t=>c.socialRepository.revise(t,'p1','完整修订帖子',['science','revised','science']));
  await tx(t=>c.socialRepository.revise(t,'r1','完整修订回复',['reply']));
  const post=await read(v=>c.socialRepository.get('p1',v));assert.equal(post.content,'完整修订帖子');assert.equal(post.likes,'0');
  const comment=await read(v=>v.query({sql:'SELECT content FROM social_comments WHERE id=?',parameters:['r1']},{maxRows:1,maxBytes:4096}));assert.equal(comment[0].content,'完整修订回复');
  for(const filter of [{},{actorId:'b'},{authorId:'a'},{excludeAuthor:'a'},{parentId:'p1'},{tag:'revised'}]){
   const found=await read(v=>c.socialRepository.posts(v,bounds,filter));assert.ok(found.items.length>0);
   if(filter.tag)assert.deepEqual(found.items.map(x=>x.id),['p1']);
  }
  const first=await read(v=>c.socialRepository.posts(v,{...bounds,maxItems:1}));assert.ok(first.next);
  const next=await read(v=>c.socialRepository.posts(v,{...bounds,maxItems:1,cursor:first.next}));assert.notEqual(first.items[0].id,next.items[0].id);
  await assert.rejects(read(v=>c.socialRepository.posts(v,{...bounds,maxResponseBytes:1})),/SOCIAL_PAGE_BYTES|SOCIAL_ITEM_BYTES/);
  const followers=await read(v=>c.socialRepository.neighbors('a',v,{...bounds,maxItems:1},'followers'));assert.ok(followers.next);
  const remaining=await read(v=>c.socialRepository.neighbors('a',v,{...bounds,maxItems:1,cursor:followers.next},'followers'));assert.notEqual(followers.items[0].from,remaining.items[0].from);
  assert.equal((await read(v=>c.socialRepository.neighbors('b',v,bounds))).items[0].to,'a');
  const scope={actorId:'a',momentId:'query'};
  for(const [name,input,actor] of [['feed',{},'b'],['profile',{},'a'],['profile',{actorId:'b'},'a'],['network',{direction:'followers'},'a'],['network',{},'b'],['notifications',{},'a'],['notifications',{unread:true},'a'],['posts',{},'a'],['posts',{authorId:'b'},'a'],['trending',{},'a'],['trending',{time:'4',window:'4'},'a']]){
   const page=await read(v=>c.information.query('social/'+name,input,{...scope,actorId:actor},v,bounds));assert.ok(Array.isArray(page.items));assert.ok(page.responseBytes<=bounds.maxResponseBytes);
  }
  await tx(async t=>{for(const [id,actorId,specId] of [['m1','a','s1'],['m2','a','s1'],['m3','b','s2']])await c.measurementRepository.append(t,{id,actorId,specId,kind:'measurement',logicalTime:'4',value:id,original:{id},source:{kind:'native-domain-observation'},required:false})});
  for(const filter of [{},{actorId:'a'},{specId:'s1'},{actorId:'a',specId:'s1'}]){
   const page=await read(v=>c.measurementRepository.page(v,{...bounds,maxItems:1},filter));assert.ok(page.next);const tail=await read(v=>c.measurementRepository.page(v,{...bounds,maxItems:1,cursor:page.next},filter));assert.notEqual(page.items[0].id,tail.items[0].id);
  }
 }finally{await held?.release();await f.host.close()}
});
