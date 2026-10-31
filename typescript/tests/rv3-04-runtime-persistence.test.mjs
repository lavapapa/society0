import test from 'node:test';
import assert from 'node:assert/strict';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {fixture,assistant,selection,extensions} from './pr08-support.mjs';

const model={id:'offline',name:'offline',api:'openai-completions',provider:'offline',baseUrl:'http://127.0.0.1:1/v1',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256};
const body=selectionValue=>({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'x',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:selectionValue,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]});
const sinkOf=(persist=async()=>{})=>new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},persist,async()=>{},()=>{});
async function consume(stream){try{for await(const _ of stream){}}catch{}}
function nativeFailure(models){return {getModel:()=>model,streamSimple:()=>({async *[Symbol.asyncIterator](){},async result(){throw Object.assign(Error('NATIVE_STREAM_DOWN'),{code:'NATIVE_STREAM_DOWN'})}})}};
function fakeProvider(run,ledger,native=nativeFailure()) {
  return new PiModelProvider(native,ledger,{runId:'pr07',providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:2000});
}

test('RV3-04-T02 observer delivery stays non-critical',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    provider=new ReplayProvider({sum:[assistant([{type:'text',text:'ok'}])]});
    const context=run.context('rv304-observe');
    prepared=await provider.prepare({provider:'replay/v1',model:'sum'},context);
    let observed=0;const configured=extensions();configured.observer=selection([{observe:async()=>{observed++}}]);
    const runtime=new PiRuntime();
    const outcome=await runtime.run(body(prepared.value),context,sinkOf(),configured);
    assert.equal(outcome.reason,'success');
    assert.ok(observed>0);
    await runtime.drain('done');
    await prepared.release();prepared=undefined;
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});

test('RV3-04-T02 sqlite commit unknown from sink is persistence_unknown',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    provider=new ReplayProvider({sum:[assistant([{type:'text',text:'ok'}])]});
    const context=run.context('rv304-sqlite');
    prepared=await provider.prepare({provider:'replay/v1',model:'sum'},context);
    let frames=0;
    const sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{frames++;throw Object.assign(Error('commit unknown'),{code:'SQLITE_COMMIT_UNKNOWN'})},async()=>{},()=>{});
    const runtime=new PiRuntime();
    const outcome=await runtime.run(body(prepared.value),context,sink,extensions());
    assert.equal(outcome.reason,'persistence_unknown');
    assert.ok(frames>0);
    await runtime.drain('done');
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});

test('RV3-04-T01 native stream rejection settles unknown',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    provider=fakeProvider(run,run.ctx.attempts);
    const context=run.context('rv304-native-failure');
    prepared=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    const stream=await prepared.value.streamFn(prepared.value.model,{messages:[{role:'user',content:'x',timestamp:0}]},{});
    await consume(stream);
    await assert.rejects(prepared.value.drain(),error=>error.code==='NATIVE_STREAM_DOWN');
    const settlements=[...(prepared.value.attemptSettlements??[])];
    assert.equal(settlements.length,1);
    assert.equal(settlements[0].status,'unknown');
    const probe=await run.ctx.attempts.probe({lineage:run.ctx.attempts.authority().lineage,operationId:context.operationId+'/model/1',attempt:0});
    assert.equal(probe.record.status,'unknown');
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});

test('RV3-04-T01 settlement write failure is attempted once without settlement',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    const actual=run.ctx.attempts;let unknowns=0,settles=0;
    const ledger={evidenceChunkBytes:actual.evidenceChunkBytes,authority:()=>actual.authority(),claim:(...args)=>actual.claim(...args),settle:(...args)=>{settles++;return actual.settle(...args)},stageEvidence:(...args)=>actual.stageEvidence(...args),markUnknown:async()=>{unknowns++;throw Object.assign(Error('LEDGER_DOWN'),{code:'LEDGER_DOWN'})}};
    provider=fakeProvider(run,ledger);
    const context=run.context('rv304-settlement-failure');
    prepared=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    const stream=await prepared.value.streamFn(prepared.value.model,{messages:[{role:'user',content:'x',timestamp:0}]},{});
    await consume(stream);
    await assert.rejects(prepared.value.drain(),error=>error.code==='NATIVE_STREAM_DOWN');
    assert.equal(unknowns,1);
    assert.equal(settles,0);
    assert.equal(prepared.value.attemptSettlements?.length??0,0);
    const pending=await actual.probe({lineage:actual.authority().lineage,operationId:context.operationId+'/model/1',attempt:0});
    assert.equal(pending.record.status,'pending');
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});
