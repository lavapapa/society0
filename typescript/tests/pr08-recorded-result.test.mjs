import test from 'node:test';
import assert from 'node:assert/strict';
import {setup} from './pr07-support.mjs';
import {ModelProviders} from '../.build/core/dist/model-providers/index.js';
import ModelReplay from '../.build/core/dist/model-replay/index.js';
const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
const message={role:'assistant',content:[{type:'text',text:'first recorded result'}],api:'openai-completions',provider:'replay',model:'recorded',usage,stopReason:'stop',timestamp:0};
test('PR08-T04 missing second recorded model result reports absence with zero fetch sends',async t=>{
 const run=await setup();t.after(()=>run.close());
 await run.host.ctx.plugin(ModelProviders,{});await run.host.ctx.plugin(ModelReplay,{name:'replay/recorded',fixtures:{recorded:[message]}});
 let ctx;await run.host.ctx.plugin({inject:['modelProviders'],apply(c){ctx=c}});
 const previous=globalThis.fetch;let sends=0,held;
 globalThis.fetch=async()=>{sends++;throw Error('unexpected network dispatch from recorded provider')};
 try{
  held=await ctx.modelProviders.prepare({provider:'replay/recorded',model:'recorded'},run.context('recorded-results'));
  const input={messages:[{role:'user',content:'recorded request',timestamp:0}]};
  const first=await held.value.streamFn(held.value.model,input,{});assert.deepEqual(await first.result(),message);
  await assert.rejects(async()=>{const second=await held.value.streamFn(held.value.model,input,{});await second.result()},/REPLAY_FIXTURE_EXHAUSTED/);
  assert.equal(sends,0);t.diagnostic(JSON.stringify({availableResults:1,requestedResults:2,missing:'REPLAY_FIXTURE_EXHAUSTED',fetchSends:sends}));
 }finally{globalThis.fetch=previous;await held?.release()}
});
