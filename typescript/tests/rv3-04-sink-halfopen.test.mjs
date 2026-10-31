// 前提（Native 调度，源码行号对应当前 checkout）：
// - activation.ts:23 `emit` 返回真实 `sink.append`；pi-agent-core agent-loop.js 在
//   streamAssistantResponse（:260）内逐事件 `await emit(...)`（:286/:300/:319/:332）。
// - 工具 update 透传在 activation.ts:12 bindTool；agent-loop.js 在 message_end 之后
//   （:141 → :165 executeToolCalls）才执行工具，因此工具阶段的并发 emit 发生在模型终态之后。
// - 预算判定在 activation.ts:20 eventBytes，使用 sink.limits。
// 结论：模型流自身无法把 pending 冲到 2；本用例用真实 Sink 并发触发 INFERENCE_SINK_BUDGET，
// 再把该真实错误作为协议层故障注入的 abort reason，验证半开 SSE 的物理 unknown 优先于
// budget/cancelled。原“模型流预算中止同时上游半开”的因果在当前调度下不可达。
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
async function connect(port,ledger,fetchImpl) {
  const credentials=new InMemoryCredentialStore();
  await credentials.modify('offline',async()=>({type:'api_key',key:'fixture-key'}));
  const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  models.setProvider(createProvider({id:'offline',models:[modelFor(port)],auth:{apiKey:envApiKeyAuth('explicit',[])},api:completions}));
  return new PiModelProvider(models,ledger,{runId:'pr07',providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:5000,...(fetchImpl?{fetch:fetchImpl}:{})});
}
async function withServer(handler){const server=createServer(handler);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return {server,port:server.address().port,close:async()=>{server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve))}}}

test('RV3-04-T02b real sink budget rejection and protocol abort on half-open SSE',async()=>{
  const run=await fixture();let provider,prepared,runtime,server;
  try {
    const sinkContext=run.context('rv304-t02b-sink');
    const gate=Promise.withResolvers();
    const sink=new OrderedInferenceSink({maxPendingEvents:2,maxPendingBytes:65536},async frame=>{if(frame.sequence===1)await gate.promise},async()=>{},()=>{});
    const first=sink.append({type:'turn_start'},sinkContext),second=sink.append({type:'turn_start'},sinkContext);
    let budgetError;
    try { await sink.append({type:'turn_start'},sinkContext); assert.fail('expected sink budget rejection'); } catch (error) { budgetError=error; }
    assert.equal(budgetError.code,'INFERENCE_SINK_BUDGET');
    gate.resolve();await Promise.allSettled([first,second]);
    await assert.rejects(sink.drain(),error=>error.code==='INFERENCE_SINK_BUDGET');

    let requests=0,upstreamEnded=false,current;
    server=await withServer((req,res)=>{requests++;req.resume();current=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write('data: '+JSON.stringify({id:'x',object:'chat.completion.chunk',created:0,model:'offline',choices:[{index:0,delta:{content:'half'},finish_reason:null}]})+'\n\n');res.on('close',()=>{upstreamEnded=res.writableEnded})});
    const headers=Promise.withResolvers();
    const routeFetch=async(input,init)=>{const response=await fetch(input,init);headers.resolve();return response};
    provider=await connect(server.port,run.ctx.attempts,routeFetch);
    const context=run.context('rv304-t02b');
    prepared=await provider.prepare({provider:'pi/v1',model:'offline'},context);
    runtime=new PiRuntime();
    const abort=new AbortController();
    const started=Promise.withResolvers();
    const runtimeSink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async frame=>{if(frame.event.type==='message_start')started.resolve()},async()=>{},error=>abort.abort(error));
    const work=runtime.run({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'half-open',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:prepared.value,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]},{...context,signal:AbortSignal.any([context.signal,abort.signal])},runtimeSink,extensions());
    await Promise.race([started.promise,headers.promise]);
    await headers.promise;
    await new Promise(resolve=>setTimeout(resolve,50));
    abort.abort(budgetError);
    const outcome=await work;
    assert.equal(outcome.reason,'transport_unknown');
    assert.equal(outcome.status,'incomplete');
    const settlements=[...(prepared.value.attemptSettlements??[])];
    assert.equal(settlements.length,1);
    assert.equal(settlements[0].status,'unknown');
    assert.equal(settlements[0].httpStatus,200);
    assert.equal(upstreamEnded,false);
    assert.equal(current.writableEnded,false);
    assert.equal(requests,1);
    const identity={lineage:run.ctx.attempts.authority().lineage,operationId:context.operationId+'/model/1',attempt:0};
    const probe=await run.ctx.attempts.probe(identity);
    assert.equal(probe.record.status,'unknown');
    await runtime.drain('done');runtime=undefined;
  } finally {
    await prepared?.release().catch(()=>{});
    await provider?.close();
    await runtime?.drain('done').catch(()=>{});
    await server?.close();
    await run.close();
  }
});
