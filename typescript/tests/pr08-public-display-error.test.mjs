import test from 'node:test';
import assert from 'node:assert/strict';
import {setup,request,read} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';
import {ModelProviders} from '../.build/core/dist/model-providers/index.js';
import ModelReplay from '../.build/core/dist/model-replay/index.js';
import {AgentRuntimes} from '../.build/core/dist/agent-runtimes/index.js';
import AgentRuntimePi from '../.build/core/dist/agent-runtime-pi/index.js';
import InferenceDefaults from '../.build/core/dist/inference-defaults/index.js';
import {DriverRegistry} from '../.build/core/dist/drivers/index.js';
import {DriverLlm} from '../.build/core/dist/driver-llm/index.js';
import {INFERENCE_REQUEST,INFERENCE_TOOL_POLICY} from '../.build/core/dist/contracts/inference.js';
const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
const assistant=(content,stopReason='stop')=>({role:'assistant',content,api:'openai-completions',provider:'replay',model:'display',usage,stopReason,timestamp:0});
test('PR08-T24 public actions_execute preserves durable completion through presentation error',async t=>{
 const run=await setup({migrations:[actorToolsMigration({test:'public-display'}),{owner:'public-display',from:0,to:1,after:[],source:{test:'public-display'},statements:[{sql:'CREATE TABLE display_sums(id TEXT PRIMARY KEY NOT NULL,total INTEGER NOT NULL)'}]}]});
 t.after(()=>run.close());
 for(const plugin of [Prompts,ContextMount,ExplorationShell,ActorTools,ModelProviders,AgentRuntimes,AgentRuntimePi,InferenceDefaults,DriverRegistry])await run.host.ctx.plugin(plugin,{});
 const shown=[];let ctx;
 await run.host.ctx.plugin({inject:['actions','slots','drivers','database'],apply(c){
  ctx=c;
  c.actions.register({name:'record_sum',effect:'world',inputSchema:{type:'object',properties:{a:{type:'number'},b:{type:'number'}},required:['a','b']},outputSchema:{type:'object',properties:{sum:{type:'number'}},required:['sum']},available:async()=>true,execute:async(input,context)=>{
   const operationId=context.operationId+'/sql',value={sum:input.a+input.b};
   await context.transaction(request(operationId),async tx=>{await tx.execute({sql:'INSERT INTO display_sums VALUES(?,?)',parameters:[operationId,value.sum]});return value});
   return {operationId,status:'completed',value,original:input,artifacts:[]};
  }});
  c.slots.contribute(INFERENCE_TOOL_POLICY,{id:'display-error',value:{after:async()=>({isError:true,content:[{type:'text',text:'presentation override'}],details:{status:'rejected'}})}});
  c.slots.contribute(INFERENCE_REQUEST,{id:'observe-display',value:{prepare:async(input,next)=>{for(const message of input.context.messages)if(message.role==='toolResult')shown.push(structuredClone(message));return next(input)}}});
 }});
 await run.host.ctx.plugin(ModelReplay,{name:'replay/public-display',fixtures:{display:[assistant([{type:'toolCall',id:'sum',name:'act',arguments:{action:'record_sum',input:{a:20,b:22}}}],'toolUse'),assistant([{type:'text',text:'42'}])]}});
 await run.host.ctx.plugin(DriverLlm,{name:'llm/public-display',provider:'replay/public-display',model:'display',requiredActions:[{name:'record_sum',minimumCompleted:1}],maxRequests:3,maxToolCalls:1});
 const context=run.context('public-display'),driver=await ctx.drivers.acquire('llm/public-display',context);let outcome;
 try{outcome=await driver.value.activate({id:'display',actorId:context.actorId,momentId:context.momentId,source:'test',driver:'llm/public-display',input:'20+22'},context)}finally{await driver.release()}
 assert.equal(outcome.status,'completed',JSON.stringify(outcome));
 const presented=shown.find(message=>message.toolCallId==='sum');assert(presented);assert.equal(presented.isError,true);assert.equal(presented.content[0].text,'presentation override');
 assert.equal(outcome.evidence.actionReceipts.length,1);const fact=outcome.evidence.actionReceipts[0];
 assert.equal(fact.toolCallId,'sum');assert.equal(fact.action,'record_sum');assert.equal(fact.receipt.status,'completed');assert.equal(fact.receipt.value.sum,42);
 assert(outcome.requiredOperations.length>3);for(const id of outcome.requiredOperations)assert.equal((await ctx.database.probe(id)).status,'committed');
 const rows=await read(ctx,view=>view.query({sql:'SELECT CAST(total AS TEXT) AS total FROM display_sums'},{maxRows:2,maxBytes:1024}));assert.deepEqual(rows.map(row=>row.total),['42']);
});
