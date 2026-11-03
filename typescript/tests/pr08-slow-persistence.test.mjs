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
