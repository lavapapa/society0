import {test} from 'node:test';import assert from 'node:assert/strict';
import {createHost} from '../.build/host/dist/index.js';
import {Context} from '../.build/cordis/dist/index.js';
import DiagnosticSink from '../.build/host/dist/diagnostics/index.js';
import TerminationPolicy from '../.build/core/dist/termination/index.js';
import {LocalArtifacts} from '../.build/core/dist/artifacts-local/store.js';
import {setup,identity,vector} from './pr09-support.mjs';
import {ExactMemoryIndex} from '../.build/extras/dist/vector-exact/index.js';
import VectorIndex from '../.build/extras/dist/vector-index/index.js';

test('redirect-limits/diagnostics historyLimit is enforced and reported as omitted',async()=>{
 const sink=new DiagnosticSink(new Context(),{historyLimit:2});
 for(const code of ['A','B','C'])sink.report({code,message:code,phase:'task'});
 const snapshot=sink.snapshot();
 assert.equal(snapshot.errors.length,2);
 assert.deepEqual(snapshot.errors.map(item=>item.code),['B','C']);
 assert.equal(snapshot.omitted,1);
});

test('redirect-limits/termination onIncomplete selects pause or fail',async()=>{
 const paused=await createHost({boot:[]});
 const failed=await createHost({boot:[]});
 try{
  await paused.ctx.plugin(TerminationPolicy);
  await failed.ctx.plugin(TerminationPolicy,{onIncomplete:'fail'});
  const input={kind:'advance',result:{status:'incomplete'},logicalTime:1};
  assert.equal(paused.ctx.terminationPolicy.evaluate(input).action,'pause');
  assert.equal(failed.ctx.terminationPolicy.evaluate(input).action,'fail');
  assert.equal(failed.ctx.terminationPolicy.evaluate({...input,result:{status:'waiting'}}).action,'continue');
 }finally{await paused.close();await failed.close()}
});

test('redirect-limits/vector-index maxLag admits a bounded stale revision and rejects beyond it',async()=>{
 const f=await setup();
 try{
  assert.equal(VectorIndex.Config({}).maxLag,0);
  const opts={generation:'projection-lag',space:'experience',identity,distance:'euclidean',maxItems:100,maxBytes:65536,maxBatchItems:4,maxQueryItems:100};
  const index=new ExactMemoryIndex({...opts,runId:f.runId});f.ctx.vectorProviders.register('memory-exact',index);
  const seed=[];for(let i=0;i<3;i++){const id='lag'+i,v=vector(id,i);await f.ctx.attempts.claim(v.origin.attempt,{kind:'fixture-vector',id});await f.ctx.attempts.settle(v.origin.attempt,{vector:v.vector});seed.push([id,v])}
  const base=await f.write('lag-seed',async tx=>{for(const [id,v] of seed)await f.ctx.memoryRepository.append(tx,{id,actorId:'a',threadId:f.threadId,body:{id},artifacts:[]},v)});
  const pendingId='lag-pending',pendingVector=vector(pendingId,9);await f.ctx.attempts.claim(pendingVector.origin.attempt,{kind:'fixture-vector',id:pendingId});await f.ctx.attempts.settle(pendingVector.origin.attempt,{vector:pendingVector.vector});
  const next=await f.write('lag-pending',tx=>f.ctx.memoryRepository.append(tx,{id:pendingId,actorId:'a',threadId:f.threadId,body:{id:pendingId},artifacts:[]},pendingVector));
  const lag=Number(BigInt(next.receipt.revision)-BigInt(base.receipt.revision));assert.ok(lag>=1);
  await f.ctx.plugin(VectorIndex,{batchRows:3,maxLag:lag});
  await f.ctx.vectorIndex.catchUp(base.receipt.revision);
  assert.equal((await f.ctx.vectorIndex.query([0,1],base.receipt.revision)).length,3);
  assert.equal((await f.ctx.vectorIndex.query([0,1],next.receipt.revision)).length,3);
  const farId='lag-far',farVector=vector(farId,10);await f.ctx.attempts.claim(farVector.origin.attempt,{kind:'fixture-vector',id:farId});await f.ctx.attempts.settle(farVector.origin.attempt,{vector:farVector.vector});
  const beyond=await f.write('lag-far',tx=>f.ctx.memoryRepository.append(tx,{id:farId,actorId:'a',threadId:f.threadId,body:{id:farId},artifacts:[]},farVector));
  await assert.rejects(f.ctx.vectorIndex.query([0,1],beyond.receipt.revision),/VECTOR_INDEX_NOT_READY/);
 }finally{await f.close()}
});

test('redirect-limits/LocalArtifacts has no unused collect operation',async()=>{
 assert.equal('collect' in LocalArtifacts.prototype,false);
});
