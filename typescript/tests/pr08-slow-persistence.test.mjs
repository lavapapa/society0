import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,appendFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setup} from './pr07-support.mjs';
import {ModelProviders} from '../.build/core/dist/model-providers/index.js';
import ModelReplay from '../.build/core/dist/model-replay/index.js';
import {AgentRuntimes} from '../.build/core/dist/agent-runtimes/index.js';
import AgentRuntimePi,{acquireExtensions,OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/index.js';
import InferenceDefaults from '../.build/core/dist/inference-defaults/index.js';
const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
const message={role:'assistant',content:[{type:'text',text:'42'}],api:'openai-completions',provider:'replay',model:'fast',usage,stopReason:'stop',timestamp:0};
for(const failWrite of [false,true])test('PR08-T25 fast model waits for slow physical sink '+(failWrite?'write failure':'success'),async t=>{
 const run=await setup();t.after(()=>run.close());
 const directory=await mkdtemp(join(tmpdir(),'pr08-slow-sink-')),log=join(directory,'frames.jsonl');
 for(const plugin of [ModelProviders,AgentRuntimes,AgentRuntimePi,InferenceDefaults])await run.host.ctx.plugin(plugin,{});
 await run.host.ctx.plugin(ModelReplay,{name:'replay/slow-sink',fixtures:{fast:[message]}});
 let ctx;await run.host.ctx.plugin({inject:['modelProviders','agentRuntimes','slots'],apply(c){ctx=c}});
 const context=run.context('slow-sink'),prepared=await ctx.modelProviders.prepare({provider:'replay/slow-sink',model:'fast'},context),runtime=await ctx.agentRuntimes.acquire('pi/v1',context),extensions=await acquireExtensions(ctx,context);
 const gate=Promise.withResolvers(),entered=Promise.withResolvers(),errors=[];let complete=false,finished=false,work;
 const original=prepared.value.streamFn;
 const selected={...prepared.value,streamFn:(...args)=>{const stream=original(...args);void stream.result().then(()=>{complete=true});return stream}};
 const sink=new OrderedInferenceSink({maxPendingEvents:4,maxPendingBytes:65536},async frame=>{
  const slow=frame.event.type==='message_start'&&frame.event.message.role==='assistant';
  if(slow){entered.resolve();await gate.promise}
  await appendFile(failWrite&&slow?directory:log,JSON.stringify(frame)+'\n');
 },async()=>{},error=>errors.push(error.code??String(error)));
 try{
  work=runtime.value.run({threadId:'slow',interactionId:'slow',history:[],prompts:[{role:'user',content:'answer',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:selected,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]},context,sink,extensions);
  void work.then(()=>{finished=true},()=>{finished=true});
  await Promise.race([entered.promise,work.then(()=>{throw Error('missing assistant persistence barrier')})]);
  const held=sink.diagnostics();assert.equal(complete,true);assert.equal(finished,false);assert(held.pending>0&&held.pending<=4);assert(held.bytes>0&&held.bytes<=65536);
  gate.resolve();const outcome=await work;await runtime.value.drain('slow sink test');
  assert.equal(outcome.status,failWrite?'incomplete':'completed');if(failWrite)assert.equal(outcome.reason,'persistence_error');
  const drained=sink.diagnostics();assert.equal(drained.pending,0);assert.equal(drained.bytes,0);assert.equal(drained.failed,failWrite);
  const frames=(await readFile(log,'utf8')).trim().split('\n').map(line=>JSON.parse(line));assert(frames.length>0);assert.deepEqual(frames.map(frame=>frame.sequence),frames.map((_,index)=>index+1));
  if(failWrite)assert(errors.includes('EISDIR'));else{assert.deepEqual(errors,[]);assert.equal(frames.at(-1).event.type,'agent_end')}
  t.diagnostic(JSON.stringify({directory,failWrite,modelCompleteBeforeRelease:true,held,drained,reason:outcome.reason,persisted:frames.length,errors}));
 }finally{
  gate.resolve();await work?.catch(()=>{});await extensions.release();await runtime.release();await prepared.release();
 }
});


test('PR08-T25 actual tool progress completes while first progress persistence is held',async t=>{
 const {Type}=await import('@earendil-works/pi-ai');
 const run=await setup();t.after(()=>run.close());
 const directory=await mkdtemp(join(tmpdir(),'pr08-progress-sink-')),log=join(directory,'frames.jsonl'),effects=join(directory,'effects.jsonl');
 for(const plugin of [ModelProviders,AgentRuntimes,AgentRuntimePi,InferenceDefaults])await run.host.ctx.plugin(plugin,{});
 const call={...message,model:'progress',stopReason:'toolUse',content:[{type:'toolCall',id:'progress-call',name:'file_progress',arguments:{}}]};
 await run.host.ctx.plugin(ModelReplay,{name:'replay/progress-sink',fixtures:{progress:[call,{...message,model:'progress'}]}});
 let ctx;await run.host.ctx.plugin({inject:['modelProviders','agentRuntimes','slots'],apply(c){ctx=c}});
 const context=run.context('progress-sink'),prepared=await ctx.modelProviders.prepare({provider:'replay/progress-sink',model:'progress'},context),runtime=await ctx.agentRuntimes.acquire('pi/v1',context),extensions=await acquireExtensions(ctx,context);
 const gate=Promise.withResolvers(),entered=Promise.withResolvers(),toolDone=Promise.withResolvers(),errors=[];let finished=false,work,heldOnce=false;
 const tool={name:'file_progress',label:'file progress',description:'write three actual file effects and emit progress',parameters:Type.Object({}),execute:async(id,args,signal,onUpdate)=>{
  for(let step=1;step<=3;step++){await appendFile(effects,JSON.stringify({step})+'\n');onUpdate({content:[{type:'text',text:'progress '+step}],details:{step,status:'completed'}})}
  toolDone.resolve();return {content:[{type:'text',text:'three file writes completed'}],details:{steps:3}};
 }};
 const sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async frame=>{
  if(frame.event.type==='tool_execution_update'&&!heldOnce){heldOnce=true;entered.resolve();await gate.promise}
  await appendFile(log,JSON.stringify(frame)+'\n');
 },async()=>{},error=>errors.push(error.code??String(error)));
 try{
  work=runtime.value.run({threadId:'progress',interactionId:'progress',history:[],prompts:[{role:'user',content:'write the file and report progress',timestamp:0}],tools:[tool],actionReceipts:{read:async()=>[]},selection:prepared.value,budget:{maxRequests:2,maxToolCalls:1},requiredActions:[]},context,sink,extensions);
  void work.then(()=>{finished=true},()=>{finished=true});
  await Promise.race([Promise.all([entered.promise,toolDone.promise]),work.then(()=>{throw Error('missing progress persistence barrier')})]);
  assert.equal(finished,false);assert.deepEqual((await readFile(effects,'utf8')).trim().split('\n').map(line=>JSON.parse(line).step),[1,2,3]);
  const held=sink.diagnostics();assert(held.pending>0&&held.pending<=16);assert(held.bytes>0&&held.bytes<=65536);
  gate.resolve();const outcome=await work;await runtime.value.drain('actual tool progress');assert.equal(outcome.status,'completed',JSON.stringify(outcome));assert.deepEqual(outcome.actionReceipts,[]);
  const frames=(await readFile(log,'utf8')).trim().split('\n').map(line=>JSON.parse(line));assert.deepEqual(frames.map(frame=>frame.sequence),frames.map((_,index)=>index+1));const updates=frames.filter(frame=>frame.event.type==='tool_execution_update'),end=frames.find(frame=>frame.event.type==='tool_execution_end');assert.equal(updates.length,3);assert(end);assert(updates.at(-1).sequence<end.sequence);assert.deepEqual(errors,[]);assert.equal(sink.diagnostics().pending,0);assert.equal(sink.diagnostics().bytes,0);
  t.diagnostic(JSON.stringify({directory,actualFileWrites:3,persistedUpdates:updates.length,durableActionReceipts:outcome.actionReceipts.length,held,drained:sink.diagnostics()}));
 }finally{gate.resolve();await work?.catch(()=>{});await extensions.release();await runtime.release();await prepared.release()}
});
