import test from 'node:test';
import assert from 'node:assert/strict';
import {Type} from '@earendil-works/pi-ai';
import {setup} from './pr07-support.mjs';
import {assistant,extensions} from './pr08-support.mjs';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';

async function harness(t,register){
  const run=await setup({migrations:[actorToolsMigration({test:'a-t1-outcome'})]});
  t.after(()=>run.close());
  for(const plugin of [Prompts,ContextMount,ExplorationShell,ActorTools])await run.host.ctx.plugin(plugin);
  let ctx;await run.host.ctx.plugin({inject:['actions','actorTools'],apply(c){ctx=c;register?.(c)}});
  const context=run.context('a-t1-outcome');
  const binding=await ctx.actorTools.bind(context);
  return {run,ctx,context,binding};
}
const invokeTool=binding=>binding.value.tools.find(tool=>tool.name==='act');
async function runModel(t,{context,binding,tools,fixture,requiredActions=[],maxRequests=3}){
  const runtime=new PiRuntime();
  t.after(()=>runtime.drain('a-t1 fixture'));
  const provider=new ReplayProvider({fixture});
  t.after(()=>provider.close());
  const held=await provider.prepare({provider:'replay/v1',model:'fixture'},context);
  t.after(()=>held.release());
  const sink=new OrderedInferenceSink({maxPendingEvents:64,maxPendingBytes:1048576},async()=>{},async()=>{},()=>{});
  return runtime.run({threadId:'a-t1-thread',interactionId:'a-t1-interaction',history:[],prompts:[{role:'user',content:'act',timestamp:0}],tools,actionReceipts:binding.value.actionReceipts,selection:held.value,budget:{maxRequests,maxToolCalls:4},requiredActions},context,sink,extensions());
}
test('A-T1 bash non-zero exit stays a completed tool result',async t=>{
  const h=await harness(t);
  try{
    const outcome=await runModel(t,{...h,tools:h.binding.value.tools,fixture:[assistant([{type:'toolCall',id:'bash-1',name:'bash',arguments:{command:"printf 'present\\n' >/workspace/a-t1.txt; exit 1"}}],'toolUse'),assistant([{type:'text',text:'done'}])]});
    assert.equal(outcome.status,'completed',JSON.stringify(outcome));
    assert.equal(outcome.reason,'success');
    const receipt=outcome.actionReceipts[0].receipt;
    assert.equal(receipt.status,'completed',JSON.stringify(receipt));
    assert.equal(receipt.value.exitCode,1);
  }finally{await h.binding.release()}
});
test('A-T1 recoverable rejected action does not fail activation and later act succeeds',async t=>{
  const h=await harness(t,c=>{
    c.actions.register({name:'blocked.action',effect:'read',inputSchema:{type:'object'},outputSchema:{},available:async()=>false,execute:async()=>{throw Error('must not execute')}});
    c.actions.register({name:'good.action',effect:'read',inputSchema:{type:'object'},outputSchema:{},available:async()=>true,execute:async(input,context)=>({operationId:context.operationId,status:'completed',value:{ok:true},original:null,artifacts:[]})});
  });
  try{
    const outcome=await runModel(t,{...h,tools:[invokeTool(h.binding)],requiredActions:[{name:'good.action',minimumCompleted:1}],fixture:[assistant([{type:'toolCall',id:'invoke-1',name:'act',arguments:{action:'blocked.action',input:{}}}],'toolUse'),assistant([{type:'toolCall',id:'invoke-2',name:'act',arguments:{action:'good.action',input:{}}}],'toolUse'),assistant([{type:'text',text:'done'}])]});
    assert.equal(outcome.status,'completed',JSON.stringify(outcome));
    assert.equal(outcome.reason,'success');
    const [blocked,good]=outcome.actionReceipts;
    assert.equal(blocked.receipt.status,'rejected');
    assert.equal(blocked.receipt.value.code,'ACTION_INELIGIBLE');
    assert.equal(good.receipt.status,'completed');
  }finally{await h.binding.release()}
});
test('A-T1 unknown attempt still reports transport_unknown',async t=>{
  const h=await harness(t,c=>{c.actions.register({name:'external.unknown',effect:'external',inputSchema:{type:'object'},outputSchema:{},available:async()=>true,execute:async()=>{throw Object.assign(Error('physical unknown'),{code:'ACTION_ATTEMPT_UNKNOWN'})}})});
  try{
    const outcome=await runModel(t,{...h,tools:[invokeTool(h.binding)],fixture:[assistant([{type:'toolCall',id:'invoke-1',name:'act',arguments:{action:'external.unknown',input:{}}}],'toolUse'),assistant([{type:'text',text:'must not run'}])]});
    assert.equal(outcome.status,'incomplete');
    assert.equal(outcome.reason,'transport_unknown');
  }finally{await h.binding.release()}
});
test('A-T1 storage unknown still reports persistence_unknown',async t=>{
  const h=await harness(t,c=>{c.actions.register({name:'world.storage',effect:'world',inputSchema:{type:'object'},outputSchema:{},available:async()=>true,execute:async()=>{throw Object.assign(Error('storage unknown'),{code:'SQLITE_COMMIT_UNKNOWN',storage:true})}})});
  try{
    const outcome=await runModel(t,{...h,tools:[invokeTool(h.binding)],fixture:[assistant([{type:'toolCall',id:'invoke-1',name:'act',arguments:{action:'world.storage',input:{}}}],'toolUse'),assistant([{type:'text',text:'must not run'}])]});
    assert.equal(outcome.status,'incomplete');
    assert.equal(outcome.reason,'persistence_unknown');
  }finally{await h.binding.release()}
});
