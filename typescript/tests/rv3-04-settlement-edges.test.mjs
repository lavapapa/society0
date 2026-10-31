import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiActivation} from '../.build/core/dist/agent-runtime-pi/activation.js';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';

const modelFor=port=>({id:'offline',name:'offline',api:'openai-completions',provider:'offline',baseUrl:'http://127.0.0.1:'+port+'/v1',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256});
async function connect(port,ledger,runId='pr07') {
  const credentials=new InMemoryCredentialStore();
  await credentials.modify('offline',async()=>({type:'api_key',key:'fixture-key'}));
  const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  models.setProvider(createProvider({id:'offline',models:[modelFor(port)],auth:{apiKey:envApiKeyAuth('explicit',[])},api:completions}));
  return new PiModelProvider(models,ledger,{runId,providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:2000});
}
async function withServer(handler){const server=createServer(handler);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return {port:server.address().port,close:()=>new Promise(resolve=>server.close(resolve))}}
const sse=(request,response)=>{request.resume();response.writeHead(200,{'content-type':'text/event-stream'});response.write('data: '+JSON.stringify({id:'x',object:'chat.completion.chunk',created:0,model:'offline',choices:[{index:0,delta:{role:'assistant',content:'ok'},finish_reason:'stop'}]})+'\n\n');response.end('data: [DONE]\n\n')};

test('RV3-04-T01 existing pending claim records unknown without dispatch',async()=>{
  const run=await fixture();const context=run.context('rv304-claim-pending');let selected,server,requests=0;
  try {
    server=await withServer((request,response)=>{requests++;sse(request,response)});
    const actual=run.ctx.attempts;
    const ledger={evidenceChunkBytes:actual.evidenceChunkBytes,authority:()=>actual.authority(),stageEvidence:(...args)=>actual.stageEvidence(...args),settle:(...args)=>actual.settle(...args),markUnknown:(...args)=>actual.markUnknown(...args),claim:async()=>({status:'existing',record:{identity:{lineage:'l',operationId:'o',attempt:0},physicalRequestId:'prior-physical',status:'pending',request:{},artifacts:[]}})};
    const provider=await connect(server.port,ledger);
    selected=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    await assert.rejects(selected.value.streamFn(selected.value.model,{messages:[{role:'user',content:'hi',timestamp:0}]},{}),/MODEL_ATTEMPT_EXISTING/);
    await assert.rejects(selected.value.drain());
    const settlements=[...(selected.value.attemptSettlements??[])];
    assert.equal(settlements.length,1);
    assert.equal(settlements[0].status,'unknown');
    assert.equal(settlements[0].physicalRequestId,'prior-physical');
    assert.equal(settlements[0].cause,'MODEL_ATTEMPT_EXISTING');
    assert.equal(requests,0);
  } finally {
    await selected?.release().catch(()=>{});
    await server?.close();
    await run.close();
  }
});

test('RV3-04-T01 archive failure becomes evidence error without settlement',async()=>{
  const run=await fixture();const context=run.context('rv304-archive');let selected,server,provider;
  try {
    server=await withServer(sse);
    const actual=run.ctx.attempts;
    const ledger={evidenceChunkBytes:actual.evidenceChunkBytes,authority:()=>actual.authority(),claim:(...args)=>actual.claim(...args),settle:(...args)=>actual.settle(...args),markUnknown:(...args)=>actual.markUnknown(...args),stageEvidence:(input,mediaType)=>{if(mediaType==='application/x-ndjson')return new Promise((resolve,reject)=>setTimeout(()=>reject(Object.assign(Error('ARCHIVE_DOWN'),{code:'ARCHIVE_DOWN'})),100));return actual.stageEvidence(input,mediaType)}};
    provider=await connect(server.port,ledger);
    selected=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    await assert.rejects(selected.value.streamFn(selected.value.model,{messages:[{role:'user',content:'hi',timestamp:0}]},{}).then(async stream=>{try{for await(const _ of stream){}}finally{await selected.value.drain()}}),cause=>cause.code==='MODEL_EVIDENCE_ERROR');
    const probe=await actual.probe({lineage:actual.authority().lineage,operationId:context.operationId+'/model/1',attempt:0});
    assert.equal(probe.record.status,'pending');
  } finally {
    await selected?.release().catch(()=>{});
    await provider?.close();
    await server?.close();
    await run.close();
  }
});

test('RV3-04-T02 persistence classification keeps commit unknown above cancellation',async()=>{
  const controller=new AbortController(),context={runId:'r',operationId:'o',signal:new AbortController().signal};
  const sink={limits:{maxPendingEvents:8,maxPendingBytes:4096},append:async()=>{},drain:async()=>{},appendUndispatched:async()=>{}};
  const empty={items:[]},extensions={request:empty,context:empty,toolPolicy:empty,completion:empty,observer:empty};
  const activation=new PiActivation({},context,sink,extensions,controller,()=>{});
  activation.failSave(Object.assign(Error('commit'),{code:'SQLITE_COMMIT_UNKNOWN'}));
  assert.equal(activation.commitUnknown,true);
  assert.equal(activation.localReason(),'persistence_unknown');
  activation.toolFailure(Object.assign(Error('segment'),{code:'INFERENCE_PERSISTENCE_ERROR',cause:{code:'SQLITE_SEGMENT_FAILED'}}));
  assert.equal(activation.commitUnknown,true);
  const persisted=new PiActivation({},context,sink,extensions,new AbortController(),()=>{});
  persisted.toolFailure(Object.assign(Error('tool'),{code:'INFERENCE_PERSISTENCE_ERROR'}));
  assert.equal(persisted.persistenceError?.message,'tool');
  assert.equal(persisted.localReason(),'persistence_error');
  const cancelled=new PiActivation({},context,sink,extensions,new AbortController(),()=>{});
  const reason=Error('cancelled');
  const aborted={runId:'r',operationId:'o',signal:AbortSignal.abort(reason)};
  const viaCancel=new PiActivation({},aborted,sink,extensions,new AbortController(),()=>{});
  viaCancel.failSave(reason);
  assert.equal(viaCancel.persistenceError,undefined);
  assert.equal(viaCancel.budgetExhausted,false);
});
