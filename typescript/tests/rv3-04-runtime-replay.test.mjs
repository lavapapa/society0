import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {fixture,assistant,extensions} from './pr08-support.mjs';

const modelFor=port=>({id:'offline',name:'offline',api:'openai-completions',provider:'offline',baseUrl:'http://127.0.0.1:'+port+'/v1',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256});
async function connect(port,ledger='pr07') {
  const credentials=new InMemoryCredentialStore();
  await credentials.modify('offline',async()=>({type:'api_key',key:'fixture-key'}));
  const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  models.setProvider(createProvider({id:'offline',models:[modelFor(port)],auth:{apiKey:envApiKeyAuth('explicit',[])},api:completions}));
  return new PiModelProvider(models,ledger,{runId:'pr07',providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:5000});
}
async function withServer(handler){const server=createServer(handler);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return {server,port:server.address().port,close:async()=>{server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve))}}}
const cut=(request,response)=>{request.resume();response.writeHead(200,{'content-type':'text/event-stream'});response.write('data: '+JSON.stringify({id:'x',object:'chat.completion.chunk',created:0,model:'offline',choices:[{index:0,delta:{role:'assistant',content:'par'},finish_reason:null}]})+'\n\n');setTimeout(()=>response.destroy(),100)};

test('RV3-04-T03 replay of unknown physical request stays unknown without resend',async()=>{
  const run=await fixture();let requests=0,selected,replay,provider,runtime;
  let bound;
  try {
    bound=await withServer((req,res)=>{requests++;cut(req,res)});
    provider=await connect(bound.port,run.ctx.attempts);
    const context=run.context('rv304-t03');
    selected=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    const stream=await selected.value.streamFn(selected.value.model,{messages:[{role:'user',content:'hi',timestamp:0}]},{});
    for await (const _ of stream) {}
    await selected.value.drain();
    const first=[...(selected.value.attemptSettlements??[])];
    assert.equal(first.length,1);assert.equal(first[0].status,'unknown');
    const identity={lineage:run.ctx.attempts.authority().lineage,operationId:context.operationId+'/model/1',attempt:0};
    const before=await run.ctx.attempts.probe(identity);
    assert.equal(before.record.status,'unknown');

    replay=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    assert.equal(replay.value.attemptSettlements?.length,0);
    runtime=new PiRuntime();
    const abort=new AbortController();
    const sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{},async()=>{},error=>abort.abort(error));
    const outcome=await runtime.run({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'replay',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:replay.value,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]},{...context,signal:AbortSignal.any([context.signal,abort.signal])},sink,extensions());
    assert.equal(outcome.reason,'transport_unknown');
    assert.equal(outcome.status,'incomplete');
    assert.equal(outcome.modelAttempts.length,1);
    assert.equal(outcome.modelAttempts[0].status,'unknown');
    assert.equal(outcome.modelAttempts[0].physicalRequestId,first[0].physicalRequestId);
    assert.deepEqual([...replay.value.attemptIds??[]],[]);
    const after=await run.ctx.attempts.probe(identity);
    assert.equal(after.record.status,'unknown');
    assert.equal(after.record.physicalRequestId,before.record.physicalRequestId);
    assert.equal(requests,1);
    await runtime.drain('done');runtime=undefined;
  } finally {
    await replay?.release().catch(()=>{});
    await selected?.release().catch(()=>{});
    await provider?.close();
    await runtime?.drain('done').catch(()=>{});
    await bound?.close();
    await run.close();
  }
});

test('RV3-04-T02c replay error reports provider_error with empty modelAttempts',async()=>{
  const run=await fixture();let prepared,provider,runtime,sink;
  try {
    provider=new ReplayProvider({error:[{...assistant([], 'error'),errorMessage:'offline provider error'}]});
    const context=run.context('rv304-t02c');
    prepared=await provider.prepare({provider:'replay/v1',model:'error'},context);
    assert.equal(prepared.value.attemptSettlements,undefined);
    runtime=new PiRuntime();
    const abort=new AbortController();
    sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{},async()=>{},error=>abort.abort(error));
    const outcome=await runtime.run({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'hi',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:prepared.value,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]},{...context,signal:AbortSignal.any([context.signal,abort.signal])},sink,extensions());
    assert.equal(outcome.status,'incomplete');
    assert.equal(outcome.reason,'provider_error');
    assert.deepEqual(outcome.modelAttempts,[]);
    await runtime.drain('done');runtime=undefined;
  } finally {
    await prepared?.release().catch(()=>{});
    await provider?.close();
    await runtime?.drain('done').catch(()=>{});
    await run.close();
  }
});
