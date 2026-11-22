import test from 'node:test';
import assert from 'node:assert/strict';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {fixture,assistant,selection,extensions} from './pr08-support.mjs';
import {modelOperationId} from '../.build/core/dist/model-pi/selection.js';

const model={id:'offline',name:'offline',api:'openai-completions',provider:'offline',baseUrl:'http://127.0.0.1:1/v1',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256};
const body=selectionValue=>({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'x',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:selectionValue,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]});
const sinkOf=(persist=async()=>{})=>new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},persist,async()=>{},()=>{});
async function consume(stream){try{for await(const _ of stream){}}catch{}}
function nativeFailure(models){return {getModel:()=>model,streamSimple:()=>({async *[Symbol.asyncIterator](){},async result(){throw Object.assign(Error('NATIVE_STREAM_DOWN'),{code:'NATIVE_STREAM_DOWN'})}})}};
function fakeProvider(run,native=nativeFailure()) {
  return new PiModelProvider(native,run.ctx.sessions.log,{runId:'pr07',providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:2000});
}

test('RV3-04-T02 observer delivery stays non-critical',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    provider=new ReplayProvider({sum:[assistant([{type:'text',text:'ok'}])]});
    const context=run.context('rv304-observe');
    prepared=await provider.prepare({session:{actorId:'a',momentId:'m'},provider:'replay/v1',model:'sum'},context);
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
    prepared=await provider.prepare({session:{actorId:'a',momentId:'m'},provider:'replay/v1',model:'sum'},context);
    let frames=0;
    const sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{frames++;throw Object.assign(Error('commit unknown'),{code:'SQLITE_COMMIT_UNKNOWN'})},async()=>{},()=>{});
    const runtime=new PiRuntime();
    const outcome=await runtime.run(body(prepared.value),context,sink,extensions());
    assert.equal(outcome.reason,'persistence_unknown');
    assert.ok(frames>0);
    await runtime.drain('done');
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});

// driver-llm 把 sink 的 abort 回调接到激活自身的 AbortController：sink 失败先中止信号，随后 emit 的拒绝才进入 failSave。
// 中止原因若就是原错误，failSave 会把它当成外部取消，真实持久化故障被误报为 cancelled。
test('RV3-04-T02 sink failure that aborts the activation signal stays persistence_error',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    provider=new ReplayProvider({sum:[assistant([{type:'text',text:'ok'}])]});
    const controller=new AbortController(),base=run.context('rv304-sink-abort'),context={...base,signal:AbortSignal.any([base.signal,controller.signal])};
    prepared=await provider.prepare({session:{actorId:'a',momentId:'m'},provider:'replay/v1',model:'sum'},context);
    const sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{throw Object.assign(Error('artifact store busy'),{code:'ARTIFACT_BUSY'})},async()=>{},error=>controller.abort(error));
    const runtime=new PiRuntime();
    const outcome=await runtime.run(body(prepared.value),context,sink,extensions());
    assert.equal(outcome.reason,'persistence_error');
    assert.equal(controller.signal.reason.code,'INFERENCE_SINK_FAILED');
    assert.equal(controller.signal.reason.cause.code,'ARTIFACT_BUSY');
    await runtime.drain('done');
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});

test('RV3-04-T01 native stream rejection settles failed',async()=>{
  const run=await fixture();let provider,prepared;
  try {
    provider=fakeProvider(run);
    const context=run.context('rv304-native-failure');
    prepared=await provider.prepare({session:{actorId:'a',momentId:'m'},provider:'pi/v1',model:'offline'},context);
    const stream=await prepared.value.streamFn(prepared.value.model,{messages:[{role:'user',content:'x',timestamp:0}]},{});
    await consume(stream);
    await assert.rejects(prepared.value.drain(),error=>error.code==='NATIVE_STREAM_DOWN');
    const settlements=[...(prepared.value.attemptSettlements??[])];
    assert.equal(settlements.length,1);
    assert.equal(settlements[0].status,'failed');
    const recorded=await run.ctx.sessions.find({actorId:'a',momentId:'m'},{operationId:modelOperationId(context,1),attempt:0});
    assert.equal(recorded.type,'attempt/failed');
    assert.equal(recorded.data.cause,'NATIVE_STREAM_DOWN');
  } finally {await prepared?.release().catch(()=>{});await provider?.close();await run.close()}
});
