import test from 'node:test';
import assert from 'node:assert/strict';
import {setup,read} from './pr07-support.mjs';
import {assistant,extensions} from './pr08-support.mjs';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';

async function harness(t,register){
  const run=await setup({migrations:[actorToolsMigration({test:'a-t2-act'})]});
  t.after(()=>run.close());
  for(const plugin of [Prompts,ContextMount,ExplorationShell,ActorTools])await run.host.ctx.plugin(plugin);
  let ctx;await run.host.ctx.plugin({inject:['actions','actorTools'],apply(c){ctx=c;register?.(c)}});
  return {run,ctx};
}
test('A-T2 act is present and the tool list is identical across actors and moments',async t=>{
  const {run,ctx}=await harness(t);
  const first=await ctx.actorTools.bind(run.context('a-t2-one','a','m1'));
  const second=await ctx.actorTools.bind(run.context('a-t2-two','b','m2'));
  try{
    assert.equal(JSON.stringify(first.value.tools),JSON.stringify(second.value.tools));
    const names=first.value.tools.map(tool=>tool.name);
    assert.ok(names.includes('act'),JSON.stringify(names));
    assert.equal(names.includes('actions_execute'),false);
    assert.equal(names.includes('actions_list'),false);
    assert.equal(new Set(names).size,names.length);
  }finally{await first.release();await second.release()}
});
test('A-T2 act executes a domain action and counts for requiredActions',async t=>{
  const {run,ctx}=await harness(t,c=>{
    c.actions.register({name:'demo.publish',effect:'read',inputSchema:{type:'object',properties:{content:{type:'string'}},required:['content'],additionalProperties:false},outputSchema:{},available:async()=>true,execute:async(input,context)=>({operationId:context.operationId,status:'completed',value:{content:input.content},original:null,artifacts:[]})});
  });
  const context=run.context('a-t2-act');
  const binding=await ctx.actorTools.bind(context);
  const runtime=new PiRuntime(),provider=new ReplayProvider({fixture:[assistant([{type:'toolCall',id:'act-1',name:'act',arguments:{action:'demo.publish',input:{content:'hello'}}}],'toolUse'),assistant([{type:'text',text:'done'}])]});
  let held;
  try{
    held=await provider.prepare({provider:'replay/v1',model:'fixture'},context);
    const sink=new OrderedInferenceSink({maxPendingEvents:64,maxPendingBytes:1048576},async()=>{},async()=>{},()=>{});
    const outcome=await runtime.run({threadId:'a-t2-thread',interactionId:'a-t2-interaction',history:[],prompts:[{role:'user',content:'publish',timestamp:0}],tools:binding.value.tools,actionReceipts:binding.value.actionReceipts,selection:held.value,budget:{maxRequests:2,maxToolCalls:2},requiredActions:[{name:'demo.publish',minimumCompleted:1}]},context,sink,extensions());
    assert.equal(outcome.status,'completed',JSON.stringify(outcome));
    assert.equal(outcome.actionReceipts.length,1);
    assert.equal(outcome.actionReceipts[0].action,'demo.publish');
    assert.equal(outcome.actionReceipts[0].receipt.status,'completed');
    assert.equal(outcome.actionReceipts[0].receipt.value.content,'hello');
  }finally{await held?.release();await provider.close();await runtime.drain('a-t2');await binding.release()}
});
test('A-T2 prompts.tools is rejected with PROMPT_TOOLS_REMOVED',async t=>{
  const run=await setup();t.after(()=>run.close().catch(()=>{}));
  await assert.rejects(async()=>{await run.host.ctx.plugin(Prompts,{tools:[{id:'publish',name:'publish',action:'social.publish'}]})},error=>String(error).includes('PROMPT_TOOLS_REMOVED'));
});
test('A-T2 built-in tools do not appear in actions.discover',async t=>{
  const {run,ctx}=await harness(t);
  const view=await run.ctx.database.snapshot({maxAgeMs:30000});
  try{
    const page=await run.ctx.actions.discover({actorId:'a',momentId:'m',logicalTime:1,query:null},view,{maxItems:100,maxResponseBytes:1048576});
    const names=page.items.map(item=>item.name);
    for(const name of ['read','find','grep','bash','act','actions_list','actions_execute'])assert.equal(names.includes(name),false,name);
  }finally{await view.close()}
});
