import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {fixture,extensions} from './pr08-support.mjs';

const modelFor=port=>({id:'offline',name:'offline',api:'openai-completions',provider:'offline',baseUrl:'http://127.0.0.1:'+port+'/v1',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256});
async function connect(port,ledger) {
  const credentials=new InMemoryCredentialStore();
  await credentials.modify('offline',async()=>({type:'api_key',key:'fixture-key'}));
  const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  models.setProvider(createProvider({id:'offline',models:[modelFor(port)],auth:{apiKey:envApiKeyAuth('explicit',[])},api:completions}));
  return new PiModelProvider(models,ledger,{runId:'pr07',providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:5000});
}
async function withServer(handler){const server=createServer(handler);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return {server,port:server.address().port,close:async()=>{server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve))}}}
const sse=(request,response)=>{request.resume();response.writeHead(200,{'content-type':'text/event-stream'});response.write('data: '+JSON.stringify({id:'x',object:'chat.completion.chunk',created:0,model:'offline',choices:[{index:0,delta:{role:'assistant',content:'ok'},finish_reason:'stop'}]})+'\n\n');response.end('data: [DONE]\n\n')};
const request=context=>{const abort=new AbortController();return {abort,request:{threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'archive',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]}}};

test('RV3-04-T01 normalized archive failure keeps ledger pending and reports persistence_error',async()=>{
  const run=await fixture();let requests=0,provider,prepared,runtime,server;
  try {
    server=await withServer((req,res)=>{requests++;sse(req,res)});
    const actual=run.ctx.attempts;
    const ledger={evidenceChunkBytes:actual.evidenceChunkBytes,authority:()=>actual.authority(),claim:(...args)=>actual.claim(...args),settle:(...args)=>actual.settle(...args),markUnknown:(...args)=>actual.markUnknown(...args),stageEvidence:(input,mediaType)=>{if(requests>0&&mediaType==='application/json')return Promise.reject(Object.assign(Error('NORMALIZED_ARCHIVE_DOWN'),{code:'NORMALIZED_ARCHIVE_DOWN'}));return actual.stageEvidence(input,mediaType)}};
    provider=await connect(server.port,ledger);
    const context=run.context('rv304-archive-normalized');
    prepared=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    runtime=new PiRuntime();
    const {abort,request:body}=request(context),sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{},async()=>{},error=>abort.abort(error));
    const outcome=await runtime.run({...body,selection:prepared.value},{...context,signal:AbortSignal.any([context.signal,abort.signal])},sink,extensions());
    assert.equal(outcome.reason,'persistence_error');
    assert.equal(outcome.modelAttempts.length,0);
    assert.equal(prepared.value.attemptSettlements?.length??0,0);
    const identity={lineage:actual.authority().lineage,operationId:context.operationId+'/model/1',attempt:0};
    const probe=await actual.probe(identity);
    assert.equal(probe.record.status,'pending');
    assert.equal(requests,1);
    await runtime.drain('done');runtime=undefined;
  } finally {
    await prepared?.release().catch(()=>{});
    await provider?.close();
    await runtime?.drain('done').catch(()=>{});
    await server?.close();
    await run.close();
  }
});

test('RV3-04-T01 payload archive failure does not dispatch and leaves ledger pending',async()=>{
  const run=await fixture();let requests=0,jsonStages=0,provider,prepared,runtime,server;
  try {
    server=await withServer((req,res)=>{requests++;sse(req,res)});
    const actual=run.ctx.attempts;
    const ledger={evidenceChunkBytes:actual.evidenceChunkBytes,authority:()=>actual.authority(),claim:(...args)=>actual.claim(...args),settle:(...args)=>actual.settle(...args),markUnknown:(...args)=>actual.markUnknown(...args),stageEvidence:(input,mediaType)=>{if(mediaType==='application/json'){jsonStages++;if(jsonStages===2)return Promise.reject(Object.assign(Error('PAYLOAD_ARCHIVE_DOWN'),{code:'PAYLOAD_ARCHIVE_DOWN'}))}return actual.stageEvidence(input,mediaType)}};
    provider=await connect(server.port,ledger);
    const context=run.context('rv304-archive-payload');
    prepared=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    runtime=new PiRuntime();
    const {abort,request:body}=request(context),sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{},async()=>{},error=>abort.abort(error));
    const outcome=await runtime.run({...body,selection:prepared.value},{...context,signal:AbortSignal.any([context.signal,abort.signal])},sink,extensions());
    assert.equal(outcome.reason,'persistence_error');
    assert.equal(outcome.modelAttempts.length,0);
    assert.equal(prepared.value.attemptSettlements?.length??0,0);
    const identity={lineage:actual.authority().lineage,operationId:context.operationId+'/model/1',attempt:0};
    const probe=await actual.probe(identity);
    assert.equal(probe.record.status,'pending');
    assert.equal(requests,0);
    assert.ok(jsonStages>=2);
    await runtime.drain('done');runtime=undefined;
  } finally {
    await prepared?.release().catch(()=>{});
    await provider?.close();
    await runtime?.drain('done').catch(()=>{});
    await server?.close();
    await run.close();
  }
});
