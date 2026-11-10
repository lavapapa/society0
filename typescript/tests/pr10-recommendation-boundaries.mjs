import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture,activation,request,bounds} from './pr10-support.mjs';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';

test('PR10 third-party SQL sources enforce item identity budgets byte LRU and concurrent prepared replacement',async()=>{
 const f=await fixture({social:false});let held;const entered=Promise.withResolvers(),release=Promise.withResolvers();let arrivals=0;
 try{
  const c=f.ctx;await c.plugin(Rankings);await c.plugin(Recommendations,{sources:['bounded/sql'],cacheBytes:1800});
  await c.plugin({inject:['recommendationSources','socialRepository'],apply(ctx){ctx.recommendationSources.register({id:'bounded/sql',version:1,dependencies:['social/content'],versions:view=>ctx.socialRepository.versions(view,['social/content']),async collect(scope,view){
   const mode=scope.query.mode;const page=await ctx.socialRepository.posts(view,{...bounds,maxItems:mode==='page'?1:10});
   let items=page.items.map(post=>({id:post.id,authorId:post.authorId,features:{content:post.content,createdTime:post.createdTime}}));
   if(mode==='duplicate')items=[items[0],items[0]];
   if(mode==='item-bytes')items=items.map(item=>({...item,features:{...item.features,extension:'x'.repeat(12000)}}));
   return {...page,items};
  },async prepare(input){
   const mode=input.scope.query.mode;
   if(mode==='concurrent'){if(++arrivals===2)entered.resolve();await release.promise}
   if(mode==='identity')return {status:'ready',input:{...input,id:'foreign-input'}};
   const extra=mode==='prepared-bytes'?12000:mode==='uncached'?2200:0;
   return {status:'ready',input:extra?{...input,items:input.items.map(item=>({...item,features:{...item.features,extension:'x'.repeat(extra)}}))}:input};
  }})}});
  held=await c.executionFence.reserve({id:'source-bounds'});
  await c.database.transaction(held.lease,request('source-seed'),async tx=>{for(const id of ['one','two'])await c.socialRepository.publish(tx,{id,authorId:'a',content:'来源完整原文 '+id,tags:[],createdTime:'1'},['b'])});
  const context=activation(f,held,'prepare-source','b');
  const prepare=(mode,limits={maxItems:10,maxBytes:8192},tag=mode)=>c.recommendations.prepare({actorId:'b',momentId:tag,query:{mode,time:'2'}},limits,context);
  for(const [mode,limits,reason] of [['duplicate',undefined,/RECOMMENDATION_DUPLICATE_ITEM/],['item-count',{maxItems:1,maxBytes:8192},/RECOMMENDATION_ITEM_BUDGET/],['prepared-bytes',undefined,/RECOMMENDATION_INPUT_BYTES/],['identity',undefined,/RECOMMENDATION_PREPARE_IDENTITY/]])await assert.rejects(prepare(mode,limits),reason);
  const itemBytes=await prepare('item-bytes');assert.equal(itemBytes.status,'ready');assert.equal(itemBytes.input.items.length,0,JSON.stringify(itemBytes.input));assert.equal(itemBytes.input.truncated,2,JSON.stringify(itemBytes.input));assert.ok(Buffer.byteLength(JSON.stringify(itemBytes.input))<=8192,JSON.stringify(itemBytes.input));
  const paged=await prepare('page');assert.equal(paged.status,'ready');assert.ok(paged.input.sourcePages[0].next);
  const input=(await prepare('minimal')).input;
  const weighted=await c.recommendations.preview(input,'weighted/v1',bounds);assert.equal(weighted.items.length,2);assert.equal(weighted.items[0].components.engagementScore,0);
  for(const [id,rank] of [['missing',()=>[]],['foreign',input=>input.items.map((item,i)=>({id:i?'foreign':item.id,score:1,components:{}}))],['duplicate',input=>input.items.map(()=>({id:input.items[0].id,score:1,components:{}}))],['nonfinite',input=>input.items.map(item=>({id:item.id,score:Infinity,components:{}}))]]){
   const dispose=c.socialRankings.register({id,source:{kind:'consumer-boundary'},configId:id,rank});
   try{await assert.rejects(c.recommendations.preview(input,id,bounds),/SOCIAL_RANKING_COVERAGE|SOCIAL_RANKING_OUTPUT/)}finally{await dispose()}
  }
  for(let i=0;i<5;i++)assert.equal((await prepare('lru',undefined,'lru-'+i)).status,'ready');
  assert.ok(c.recommendations.diagnostics().cacheBytes<=1800);
  const before=c.recommendations.diagnostics().cacheBytes;await prepare('uncached');assert.equal(c.recommendations.diagnostics().cacheBytes,before);
  const first=prepare('concurrent'),second=prepare('concurrent');await entered.promise;release.resolve();const values=await Promise.all([first,second]);assert.ok(values.every(value=>value.status==='ready'));
  const hit=(await prepare('concurrent')).input;assert.ok(values.some(value=>value.input.id===hit.id));assert.ok(c.recommendations.diagnostics().cacheBytes<=1800);
 }finally{release.resolve();await held?.release();await f.host.close()}
});
