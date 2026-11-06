import {test} from 'node:test';import assert from 'node:assert/strict';
import {fixture,assistant,tools,extensions} from './pr08-support.mjs';import {request} from './pr07-support.mjs';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {Prompts} from '../.build/core/dist/prompts/index.js';import {ContextMount} from '../.build/core/dist/mount-context/index.js';import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';import {ActorTools} from '../.build/core/dist/actor-tools/index.js';import {ModelProviders} from '../.build/core/dist/model-providers/index.js';import {AgentRuntimes} from '../.build/core/dist/agent-runtimes/index.js';import InferenceDefaults from '../.build/core/dist/inference-defaults/index.js';import {DriverRegistry} from '../.build/core/dist/drivers/index.js';import {DriverLlm} from '../.build/core/dist/driver-llm/index.js';

async function contextEnv(prompts){const run=await fixture();try{
 for(const [plugin,config] of [[Prompts,prompts],[ContextMount,{}],[ExplorationShell,{}],[ActorTools,{}],[ModelProviders,{}],[AgentRuntimes,{}],[InferenceDefaults,{}],[DriverRegistry,{}]])await run.host.ctx.plugin(plugin,config);
 let ctx;await run.host.ctx.plugin({inject:['actions','modelProviders','drivers','actorTools','threads','contextMount','prompts','database','perceptions','artifacts','attempts','agentRuntimes','slots'],apply(c){ctx=c}});
 return {run,ctx};
 }catch(error){await run.close();throw error}}

test('redirect-limits/turn context assembly over maxContextBytes becomes incomplete/context_limit and preserves the thread',async()=>{
 const {run,ctx}=await contextEnv({sections:[{id:'big',order:0,text:'x'.repeat(512)}],maxContextBytes:64});
 try{
  const provider=new ReplayProvider({limit:[assistant([{type:'text',text:'must not be called'}])]});let modelCalls=0;const prepare=provider.prepare.bind(provider);
  provider.prepare=async(...args)=>{const selected=await prepare(...args),stream=selected.value.streamFn;return {value:{...selected.value,streamFn:(...input)=>{modelCalls++;return stream(...input)}},release:()=>selected.release()}};
  ctx.modelProviders.register('replay/v1',provider);
  await run.host.ctx.plugin(DriverLlm,{name:'llm/context-limit',provider:'replay/v1',model:'limit'});
  const context=run.context('redirect-context-limit');
  const view=await ctx.database.snapshot({maxAgeMs:30000});let opened;try{opened=await ctx.threads.open(context.actorId,context.momentId,view)}finally{await view.close()}
  const seed=await ctx.threads.prepare({id:'redirect-seed',interactionId:opened.interactionId,role:'user',content:'seed history must survive',originalProviderFields:null},context.signal);
  await ctx.database.transaction(context.writeLease,request('redirect-seed'),tx=>ctx.threads.append(tx,opened.id,seed));
  const driver=await ctx.drivers.acquire('llm/context-limit',context);
  let result;try{result=await driver.value.activate({id:'context-limit',actorId:context.actorId,momentId:context.momentId,source:'test',driver:'llm/context-limit',input:'assembly limit'},context)}finally{await driver.release()}
  assert.equal(result.status,'incomplete',JSON.stringify(result));
  assert.equal(result.value.reason,'context_limit',JSON.stringify(result));
  assert.equal(result.evidence.code,'INFERENCE_CONTEXT_LIMIT');
  assert.equal(result.evidence.stage,'prompts');
  assert.ok(result.evidence.limit.bytes>64,JSON.stringify(result.evidence));
  assert.equal(result.evidence.limit.maxBytes,64);
  assert.equal(modelCalls,0);
  const after=await (async()=>{const next=await ctx.database.snapshot({maxAgeMs:30000});try{return await ctx.threads.open(context.actorId,context.momentId,next)}finally{await next.close()}})();
  assert.ok(Number(after.messageCount)>=1);
 }finally{await run.close()}
});

test('redirect-limits/driver-llm maxTurns is passed to the runtime budget',async()=>{
 const {run,ctx}=await contextEnv({});
 try{
  const seen=[];ctx.agentRuntimes.register('capture/v1',{run:async request=>{seen.push(request.budget.maxTurns);return {status:'completed',reason:'success',requests:0,actionReceipts:[],undispatched:[],attemptIds:[],modelAttempts:[]}},drain:async()=>{}});
  ctx.modelProviders.register('replay/v1',new ReplayProvider({unused:[assistant([{type:'text',text:'unused'}])]}));
  assert.equal(DriverLlm.Config({provider:'replay/v1',model:'unused'}).maxTurns,20);
  await run.host.ctx.plugin(DriverLlm,{name:'llm/turns',provider:'replay/v1',model:'unused',runtime:'capture/v1',maxTurns:3});
  const context=run.context('redirect-max-turns');
  const driver=await ctx.drivers.acquire('llm/turns',context);
  let result;try{result=await driver.value.activate({id:'turns',actorId:context.actorId,momentId:context.momentId,source:'test',driver:'llm/turns',input:'count turns'},context)}finally{await driver.release()}
  assert.equal(result.status,'completed',JSON.stringify(result));
  assert.deepEqual(seen,[3]);
 }finally{await run.close()}
});

test('redirect-limits/pi runtime stops after maxTurns even when requests remain',async()=>{
 const run=await fixture();let provider,runtime,held;
 try{
  const messages=[1,2,3].map(index=>assistant([{type:'toolCall',id:'turn-'+index,name:'record_sum',arguments:{a:index,b:1}}],'toolUse'));
  provider=new ReplayProvider({turns:messages});const context=run.context('redirect-runtime-turns');const binding=tools(run,context);held=await provider.prepare({provider:'replay/v1',model:'turns'},context);runtime=new PiRuntime();
  const sink=new OrderedInferenceSink({maxPendingEvents:64,maxPendingBytes:1048576},async()=>{},async()=>{},()=>{});
  const outcome=await runtime.run({threadId:'t',interactionId:'i',history:[{role:'user',content:'run turns',timestamp:0}],prompts:[],tools:[binding.tool],actionReceipts:binding.reader,selection:held.value,budget:{maxRequests:5,maxToolCalls:5,maxTurns:2},requiredActions:[]},context,sink,extensions());
  assert.equal(outcome.reason,'budget_exhausted',JSON.stringify(outcome));
  assert.equal(outcome.requests,2);
  assert.equal(outcome.actionReceipts.length,2);
 }finally{await held?.release();await provider?.close();await runtime?.drain('redirect maxTurns');await run.close()}
});
