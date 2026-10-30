import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';import {once} from 'node:events';
import {fixture,activation,request} from './pr10-support.mjs';
import Embeddings from '../.build/core/dist/embedding/index.js';
import EmbeddingHttp from '../.build/core/dist/embedding-http/index.js';
import Credentials from '../.build/core/dist/model-credentials/index.js';
import Providers from '../.build/extras/dist/vector-providers/index.js';
import Exact from '../.build/extras/dist/vector-exact/index.js';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';
import Semantic,{semanticMigration} from '../.build/social/dist/social-semantic/index.js';

test('PR10 actual embedding preparation detects concurrently advanced Exact projection and replays deletion pages',async()=>{
 const entered=Promise.withResolvers(),release=Promise.withResolvers();let queries=0,f,held,index;
 const server=createServer(async(req,res)=>{let body='';for await(const chunk of req)body+=chunk;const input=JSON.parse(body).input;
  if(input[0]==='{}'){queries++;entered.resolve();await release.promise}
  res.setHeader('content-type','application/json');res.end(JSON.stringify({data:input.map((text,index)=>({index,embedding:[1,0],originalText:text})),usage:{tokens:input.length}}));
 });server.listen(0,'127.0.0.1');await once(server,'listening');
 try{
  f=await fixture({social:false,migrations:[semanticMigration]});const c=f.ctx;
  const identity={provider:'boundary-http',configId:'boundary/v1',model:'boundary-vector',dimensions:2};
  await c.plugin(Embeddings);await c.plugin(Credentials);await c.plugin(EmbeddingHttp,{...identity,url:'http://127.0.0.1:'+server.address().port,limits:{maxWaitMs:1}});
  await c.plugin(Providers,{runId:f.runId});await c.plugin(Exact,{name:'social-index',generation:'g',space:'social',identity,distance:'cosine',maxItems:100,maxBytes:1048576,maxBatchItems:64,maxQueryItems:100});
  await c.plugin(Rankings);await c.plugin(Recommendations,{sources:['social/posts/v1','social/semantic/v1']});await c.plugin(Semantic,{embeddingProvider:identity.provider,vectorProvider:'social-index',batchSize:1});
  held=await c.executionFence.reserve({id:'projection-boundaries'});const context=activation(f,held,'semantic-boundary','c');
  await c.database.transaction(held.lease,request('projection-seed'),async tx=>{for(const id of ['one','two'])await c.socialRepository.publish(tx,{id,authorId:'a',content:'完整语义原文 '+id,createdTime:'1'},[])});
  for(let i=0;i<2;i++)assert.equal((await c.socialSemantic.embedPending(held.lease,{...context,operationId:'embed-'+i})).status,'ready');
  const synced=await c.socialSemantic.syncIndex(held.lease,{...context,operationId:'sync-before'});assert.equal(synced.requiredOperations.length,2);
  const pending=c.recommendations.prepare({actorId:'c',momentId:'race',query:{time:'2'}},{maxItems:10,maxBytes:65536},{...context,operationId:'query-race'});
  await entered.promise;assert.equal(c.database.diagnostics().snapshots,0);
  const expectedRevision=await c.database.readRevision();await c.database.transaction(held.lease,{...request('remove-vector'),expectedRevision},tx=>c.socialSemantic.projection.remove(tx,'one',String(BigInt(expectedRevision)+1n)));
  await c.socialSemantic.syncIndex(held.lease,{...context,operationId:'sync-after'});release.resolve();const result=await pending;assert.equal(result.status,'stale');assert.deepEqual(result.changed,['social/semantic/index']);assert.equal(queries,1);
  index=await c.vectorProviders.acquire('social-index');assert.equal(index.value.diagnostics().items,1);
  const view=await c.database.snapshot({maxAgeMs:30000});try{assert.equal((await c.socialRepository.get('one',view)).content,'完整语义原文 one');const progress=await c.socialSemantic.projection.progress(view,index.value);assert.equal(progress.after.id,'one');const found=await index.value.query([1,0],10,progress,context.signal);assert.deepEqual(found.map(item=>item.id),['two'])}finally{await view.close()}
 }finally{release.resolve();await index?.release();await held?.release();await f?.host.close();server.close();await once(server,'close')}
});
