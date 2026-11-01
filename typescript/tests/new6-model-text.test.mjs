import test from 'node:test';
import assert from 'node:assert/strict';
import {setup,request} from './pr07-support.mjs';
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
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
const assistant=(content,stopReason='stop')=>({role:'assistant',content,api:'openai-completions',provider:'replay',model:'driver',usage,stopReason,timestamp:Date.now()});
test('NEW-6-T11 malformed model toolCall id ends as provider_error',async()=>{const run=await setup({migrations:[actorToolsMigration({test:'h01'}),{owner:'h01-sum',from:0,to:1,after:[],source:{test:'h01'},statements:[{sql:'CREATE TABLE h01_sums(id TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)'}]}]});try{
 for(const [plugin,config]of [[Prompts,{sections:[],contexts:[]}],[ContextMount,{}],[ExplorationShell,{}],[ActorTools,{}],[ModelProviders,{}],[AgentRuntimes,{}],[AgentRuntimePi,{}],[InferenceDefaults,{}],[DriverRegistry,{}]])await run.host.ctx.plugin(plugin,config);
 let ctx;await run.host.ctx.plugin({inject:['actions','modelProviders','drivers','actorTools','threads','contextMount','prompts','database','perceptions','artifacts','attempts','agentRuntimes','slots'],apply(c){ctx=c;c.actions.register({name:'record_sum',effect:'world',inputSchema:{type:'object',properties:{a:{type:'number'}},required:['a'],additionalProperties:false},outputSchema:{},available:async()=>true,execute:async(input,context)=>{const operationId=context.operationId+'/sql';await context.transaction(request(operationId),async tx=>{await tx.execute({sql:'INSERT INTO h01_sums VALUES(?,?)',parameters:[operationId,1]})});return {operationId,status:'completed',value:{sum:1},original:null,artifacts:[],requiredOperations:[]}}});const provider=new ReplayProvider({driver:[assistant([{type:'toolCall',id:'bad\ud800id',name:'actions_execute',arguments:{action:'record_sum',input:{a:1}}}],'toolUse'),assistant([{type:'text',text:'done'}])]});c.modelProviders.register('replay/v1',provider)}});
 await run.host.ctx.plugin(DriverLlm,{name:'llm/v1',provider:'replay/v1',model:'driver',maxRequests:2,maxToolCalls:1});
 const driver=await ctx.drivers.acquire('llm/v1',run.context('h01-t11'));let result;try{result=await driver.value.activate({id:'t11',actorId:run.context('x').actorId,momentId:run.context('x').momentId,source:'test',driver:'llm/v1',input:'bad id'},run.context('h01-t11'))}finally{await driver.release()}
 assert.equal(result.status,'incomplete',JSON.stringify(result));assert.equal(result.value.reason,'provider_error',JSON.stringify(result));
 }finally{await run.close()}});
