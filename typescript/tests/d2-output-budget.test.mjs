import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {setup} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';
import {ModelProviders} from '../.build/core/dist/model-providers/index.js';
import {AgentRuntimes} from '../.build/core/dist/agent-runtimes/index.js';
import AgentRuntimePi from '../.build/core/dist/agent-runtime-pi/index.js';
import InferenceDefaults from '../.build/core/dist/inference-defaults/index.js';
import {DriverRegistry} from '../.build/core/dist/drivers/index.js';
import {DriverLlm} from '../.build/core/dist/driver-llm/index.js';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {INFERENCE_REQUEST} from '../.build/core/dist/contracts/inference.js';

// 核对值见 docs/model-context.md：2026-11-09 读取本地 LiteLLM 网关的 /v1/models 与 /model/info。
export const VERIFIED_CONTEXT_WINDOW=262144;
const OUTPUT_SLOT=512;
// 14500 个 ASCII 字符约 4100-4300 token；加上 header 规则与输入，估算约 4.2k。
const PROMPT_CHARS=14500;

function sse(request,response,text='ok'){
  request.resume();
  response.writeHead(200,{'content-type':'text/event-stream'});
  response.write('data: '+JSON.stringify({id:'d2',object:'chat.completion.chunk',created:0,model:'budget',choices:[{index:0,delta:{role:'assistant',content:text},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:1,total_tokens:11}})+'\n\n');
  response.end('data: [DONE]\n\n');
}

async function fixture({contextWindow,reply='ok',maxRequests=2,requiredActions=[],middleware}){
  const run=await setup({migrations:[actorToolsMigration({test:'d2-budget'})]});
  const bodies=[];
  const server=createServer(async(request,response)=>{let text='';for await(const chunk of request)text+=chunk;bodies.push(JSON.parse(text));sse(request,response,reply)});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let ctx,provider;
  try{
    for(const [plugin,config] of [[Prompts,{sections:[{id:'instructions',text:'x'.repeat(PROMPT_CHARS)}],contexts:[]}],[ContextMount,{}],[ExplorationShell,{}],[ActorTools,{}],[ModelProviders,{}],[AgentRuntimes,{}],[AgentRuntimePi,{}],[InferenceDefaults,{}],[DriverRegistry,{}]])await run.host.ctx.plugin(plugin,config);
    const credentials=new InMemoryCredentialStore();
    await credentials.modify('budget',async()=>({type:'api_key',key:'budget-fixture-key'}));
    const model={id:'budget',name:'budget',api:'openai-completions',provider:'budget',baseUrl:'http://127.0.0.1:'+server.address().port+'/v1',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow,maxTokens:2048,compat:{maxTokensField:'max_tokens'}};
    const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
    models.setProvider(createProvider({id:'budget',models:[model],auth:{apiKey:envApiKeyAuth('explicit',[])},api:completions}));
    provider=new PiModelProvider(models,run.ctx.attempts,{runId:'pr07',providerId:'budget',maxEventBytes:1048576,maxRawBytes:2097152,maxRequestBytes:1048576,maxPendingEvents:32,maxPendingBytes:1048576,options:{maxTokens:2048,maxRetries:0,transport:'sse'},timeoutMs:30000});
    await run.host.ctx.plugin({inject:['modelProviders','drivers','actorTools','threads','contextMount','prompts','database','perceptions','artifacts','attempts','agentRuntimes','slots','actions'],apply(c){ctx=c;c.modelProviders.register('http/v1',provider);if(middleware)c.slots.contribute(INFERENCE_REQUEST,{id:'d1b-middleware',value:{prepare:async(input,next)=>{middleware.calls++;const context={...input.context,messages:[...input.context.messages,{role:'user',content:'d1b-middleware-marker',timestamp:0}]};return next({...input,context})}}})}});
    await run.host.ctx.plugin(DriverLlm,{name:'llm/v1',provider:'http/v1',model:'budget',perceptions:[],requiredActions,maxRequests,maxToolCalls:1,budget:{output:OUTPUT_SLOT}});
    const base=run.directory;
    return {run,bodies,activate:async operationId=>{
      const context=run.context(operationId);
      const intent={id:operationId,actorId:context.actorId,momentId:context.momentId,source:'test',driver:'llm/v1',input:'d2 '+operationId};
      const driver=await ctx.drivers.acquire('llm/v1',context);
      try{return await driver.value.activate(intent,context)}finally{await driver.release()}
    },close:async()=>{await provider?.close();await new Promise(resolve=>server.close(resolve));await run.close()},directory:base};
  }catch(error){await provider?.close();await new Promise(resolve=>server.close(resolve));await run.close();throw error}
}

test('D2 output budget blocks a physical request whose pi-ai max_tokens falls below the output slot',{timeout:900000},async()=>{
  const env=await fixture({contextWindow:8192});
  try{
    const result=await env.activate('d2/tiny-window');
    const sent=env.bodies.map(body=>body.max_tokens??body.max_completion_tokens);
    assert.equal(env.bodies.length,0,`base 上的请求 max_tokens=${JSON.stringify(sent)}；修复后不应发出请求`);
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'BUDGET_OUTPUT_TOO_SMALL',JSON.stringify(result.value));
    const evidence=result.evidence.outputBudget;
    assert.equal(typeof evidence.estimatedPrompt,'number',JSON.stringify(result.evidence));
    assert.equal(evidence.contextWindow,8192);
    assert.equal(evidence.output,OUTPUT_SLOT);
    assert.equal(typeof evidence.safety,'number');
    assert.ok(evidence.estimatedPrompt>3584,JSON.stringify(evidence));
    assert.ok(evidence.maxTokens<OUTPUT_SLOT,JSON.stringify(evidence));
    console.log(JSON.stringify({case:'D2-tiny-window',reason:result.value.reason,requestBodies:env.bodies.length,evidence}));
  }finally{await env.close()}
});

test('D2 verified context window lets the same prompt request max_tokens at or above the output slot',{timeout:900000},async()=>{
  const env=await fixture({contextWindow:VERIFIED_CONTEXT_WINDOW});
  try{
    const result=await env.activate('d2/verified-window');
    assert.equal(env.bodies.length,1,JSON.stringify(env.bodies));
    assert.equal(result.status,'completed',JSON.stringify(result));
    assert.equal(result.value.reason,'success',JSON.stringify(result.value));
    const maxTokens=env.bodies[0].max_tokens??env.bodies[0].max_completion_tokens;
    assert.ok(maxTokens>=OUTPUT_SLOT,`max_tokens=${maxTokens}`);
    console.log(JSON.stringify({case:'D2-verified-window',status:result.status,reason:result.value.reason,maxTokens,contextWindow:VERIFIED_CONTEXT_WINDOW}));
  }finally{await env.close()}
});

test('D1b request middleware runs before the maxRequests check without persisting its transform',{timeout:900000},async()=>{
  const middleware={calls:0};
  const env=await fixture({contextWindow:VERIFIED_CONTEXT_WINDOW,reply:'{"action":"record_sum"}',maxRequests:1,requiredActions:[{name:'record_sum',minimumCompleted:1}],middleware});
  try{
    const result=await env.activate('d1b/order');
    assert.equal(middleware.calls,2,JSON.stringify({reason:result.value.reason,calls:middleware.calls}));
    assert.equal(env.bodies.length,1,JSON.stringify(env.bodies.length));
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'budget_exhausted',JSON.stringify(result.value));
    assert.equal(result.value.requests,1,JSON.stringify(result.value));
    const view=await env.run.ctx.database.snapshot({maxAgeMs:30000});
    let rows;
    try{rows=await view.query({sql:"SELECT thread_id,seq,role,payload FROM thread_messages WHERE payload LIKE '%d1b-middleware-marker%'"},{maxRows:10,maxBytes:65536})}finally{await view.close()}
    assert.equal(rows.length,0,JSON.stringify(rows));
    console.log(JSON.stringify({case:'D1b-order',middlewareCalls:middleware.calls,requestBodies:env.bodies.length,reason:result.value.reason}));
  }finally{await env.close()}
});

function findModel(value,id){
  if(Array.isArray(value)){for(const item of value){const found=findModel(item,id);if(found)return found}return undefined}
  if(value&&typeof value==='object'){
    if(value.id===id&&typeof value.contextWindow==='number')return value;
    for(const item of Object.values(value)){const found=findModel(item,id);if(found)return found}
  }
  return undefined;
}

test('D2 social profile declares the verified context window',async()=>{
  const profile=JSON.parse(await readFile(new URL('../profiles/social-sqlite.json',import.meta.url),'utf8'));
  const model=findModel(profile,'Qwen3.6-35B-A3B-FP8');
  assert.ok(model,'social-sqlite.json 中缺少 Qwen3.6-35B-A3B-FP8');
  assert.equal(model.contextWindow,VERIFIED_CONTEXT_WINDOW);
  console.log(JSON.stringify({case:'D2-profile',model:model.id,contextWindow:model.contextWindow}));
});
