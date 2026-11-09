import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHost,OwnedResources,TaskSupervisor} from '../.build/host/dist/index.js';
import CompositionStrategies from '../.build/host/dist/composition-strategies/index.js';
import SlotStrategies from '../.build/host/dist/slot-strategies/index.js';
import Slots from '../.build/host/dist/slots/index.js';
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
const assistant=(content,stopReason='stop')=>({role:'assistant',content,api:'openai-completions',provider:'replay',model:'driver',usage,stopReason,timestamp:1});
const toolTurn=(id,name,args)=>assistant([{type:'toolCall',id,name,arguments:args}],'toolUse');
const finalTurn=assistant([{type:'text',text:'done'}]);
const readMissing=toolTurn('d1-read','read',{path:'/workspace/missing'});
const actSum=toolTurn('d1-act','act',{action:'record_sum',input:{a:20,b:22}});

function actionSet(){
  const record_sum={name:'record_sum',effect:'world',inputSchema:{type:'object',properties:{a:{type:'number'},b:{type:'number'}},required:['a','b']},outputSchema:{},available:async()=>true,
    execute:async(input,context)=>{const operationId=context.operationId+'/sum';await context.transaction(request(operationId),async tx=>{await tx.execute({sql:'INSERT INTO d1_sums VALUES(?,?)',parameters:[operationId,input.a+input.b]})});return {operationId,status:'completed',value:{sum:input.a+input.b},original:input,artifacts:[]}}};
  const broken_action={name:'broken_action',effect:'world',inputSchema:{type:'object'},outputSchema:{},available:async()=>true,
    execute:async(_input,context)=>({operationId:context.operationId,status:'failed',value:{code:'ACTION_BROKEN'},original:null,artifacts:[]})};
  return [record_sum,broken_action];
}

async function fixture({script=[actSum,finalTurn],driver={},perception}={}){
  const run=await setup({migrations:[actorToolsMigration({test:'pr13-d1'}),{owner:'d1-sums',from:0,to:1,after:[],source:{test:'pr13-d1'},statements:[{sql:'CREATE TABLE d1_sums(id TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)'}]}]});
  try{
    for(const [plugin,config] of [[Prompts,{sections:[{id:'instructions',text:'Use tools then finish.'}],contexts:[]}],[ContextMount,{}],[ExplorationShell,{}],[ActorTools,{}],[ModelProviders,{}],[AgentRuntimes,{}],[AgentRuntimePi,{}],[InferenceDefaults,{}],[DriverRegistry,{}]])await run.host.ctx.plugin(plugin,config);
    let ctx;
    await run.host.ctx.plugin({inject:['actions','modelProviders','drivers','actorTools','threads','contextMount','prompts','database','perceptions','artifacts','attempts','agentRuntimes','slots'],apply(c){ctx=c;
      if(perception)c.perceptions.register(perception);
      for(const action of actionSet())c.actions.register(action);
      c.modelProviders.register('replay/v1',new ReplayProvider({driver:script}));
    }});
    await run.host.ctx.plugin(DriverLlm,{name:'llm/v1',provider:'replay/v1',model:'driver',perceptions:perception?['fixture/events']:[],requiredActions:[{name:'record_sum',minimumCompleted:1}],maxRequests:4,maxToolCalls:4,...driver});
    const activate=async operationId=>{const context=run.context(operationId);const intent={id:operationId,actorId:context.actorId,momentId:context.momentId,source:'test',driver:'llm/v1',input:'pr13-d1 '+operationId};const driver=await ctx.drivers.acquire('llm/v1',context);try{return await driver.value.activate(intent,context)}finally{await driver.release()}};
    return {run,ctx,activate,close:()=>run.close()};
  }catch(error){await run.close();throw error}
}

const row=(name,body)=>test(name,{timeout:900000},body);

row('D1 exploration error with completed action is success',async()=>{
  const env=await fixture({script:[readMissing,actSum,finalTurn]});
  try{
    const result=await env.activate('d1/exploration-success');
    assert.equal(result.status,'completed',JSON.stringify(result));
    assert.equal(result.value.reason,'success',JSON.stringify(result.value));
    assert.equal(result.value.explorationErrors,1,JSON.stringify(result.value));
    assert.equal(result.value.actFailed,0,JSON.stringify(result.value));
    assert.equal(result.value.actRecoverable,0,JSON.stringify(result.value));
    assert.equal(result.value.requiredSatisfied,true,JSON.stringify(result.value));
  }finally{await env.close()}
});

row('D1 recoverable action rejection then retry is success',async()=>{
  const rejected=toolTurn('d1-reject','act',{action:'record_sum',target:'thing:1',input:{a:20,b:22}});
  const env=await fixture({script:[rejected,actSum,finalTurn]});
  try{
    const result=await env.activate('d1/recoverable');
    assert.equal(result.status,'completed',JSON.stringify(result));
    assert.equal(result.value.reason,'success',JSON.stringify(result.value));
    assert.equal(result.value.actRecoverable,1,JSON.stringify(result.value));
    assert.equal(result.value.actFailed,0,JSON.stringify(result.value));
  }finally{await env.close()}
});

row('D1 failed action receipt is tool_failed',async()=>{
  const env=await fixture({script:[toolTurn('d1-broken','act',{action:'broken_action'}),finalTurn],driver:{requiredActions:[]}});
  try{
    const result=await env.activate('d1/broken');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'tool_failed',JSON.stringify(result.value));
    assert.equal(result.value.actFailed,1,JSON.stringify(result.value));
  }finally{await env.close()}
});

row('D1 exploration error with unsatisfied required action is required_action_failed',async()=>{
  const env=await fixture({script:[readMissing,finalTurn]});
  try{
    const result=await env.activate('d1/required');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'required_action_failed',JSON.stringify(result.value));
    assert.equal(result.value.explorationErrors,1,JSON.stringify(result.value));
    assert.equal(result.value.requiredSatisfied,false,JSON.stringify(result.value));
  }finally{await env.close()}
});

row('D1 failOnToolError all restores old tool_failed semantics',async()=>{
  const env=await fixture({script:[readMissing,actSum,finalTurn],driver:{failOnToolError:'all'}});
  try{
    const result=await env.activate('d1/all');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'tool_failed',JSON.stringify(result.value));
    assert.equal(result.value.explorationErrors,1,JSON.stringify(result.value));
  }finally{await env.close()}
});

const req=operationId=>({operationId,maxDurationMs:15000,maxStatements:100,maxInputBytes:1048576,maxChangedRows:1000,maxCapturedBytes:4194304});
const base='../.build/core/dist/';
const modules=await Promise.all(['execution-fence','sqlite','sqlite-bootstrap','sqlite-history','artifacts-local','initializers','actors','actor-eligibility','drivers','activation-mailbox','activation','clock-ticks','proposal-sequence','runtime-staged','completion-strict','checkpoint-policy','termination','generation-views','generation-binding','run-controller'].map(name=>import(base+name+'/index.js')));
const [Fence,Database,Bootstrap,History,Artifacts,Initializers,Actors,Eligibility,Drivers,Mailbox,Activation,Ticks,Sequence,Runtime,Completion,Checkpoint,Termination,Views,Binding,Runs]=modules;

async function runFixture(){
  const host=await createHost({boot:[]});const c=host.ctx;const directory=await mkdtemp(join(tmpdir(),'pr13-d1-run-'));
  for(const plugin of [OwnedResources,TaskSupervisor,CompositionStrategies,SlotStrategies,Slots,Fence.default])await c.plugin(plugin);
  await c.plugin(Database.default,{directory,runId:'run',generation:'g',maxTransactionMs:20000});
  await c.plugin(Artifacts.default,{directory:join(directory,'artifacts')});
  await c.plugin(Initializers.default);
  await c.plugin(Bootstrap.default,{maxTransactionMs:15000});
  await c.plugin(History.default,{directory,runId:'run',generation:'g',controlSequence:'0'});
  await c.plugin(Actors.default);await c.plugin(Eligibility.default);await c.plugin(Drivers.default);await c.plugin(Mailbox.default);await c.plugin(Activation.default,{});
  await c.plugin({inject:['initializers','actors'],apply(ctx){ctx.initializers.register({id:'seed',after:[],async initialize(context){await context.database.transaction(context.writeLease,req('seed'),async tx=>{await ctx.actors.create(tx,{id:'a',attributes:{count:0}})});return {operationIds:['seed']}}})}});
  await c.sqliteBootstrap.create({operationId:'create',runId:'run',directory,input:{},migrations:[Artifacts.artifactsMigration,Actors.actorsMigration],initialFrontier:{codec:'staged/1',value:'0',logicalTime:'0',completedCount:'0',pendingAcknowledgements:[]},initialControl:{generation:'g',controlSequence:'0',planId:'plan'},requiredInitializerIds:['seed']});
  const proposals=Array.from({length:2},(_,index)=>({id:'p'+index,planKind:'staged/1',payload:{phases:[{id:'phase',activations:[{id:'a'+index,actorId:'a',momentId:'t'+index,source:'agenda',driver:'summary',input:null}]}],nextCursor:String(index+1)}}));
  await c.plugin(Ticks.default);await c.plugin(Sequence.default,{proposals});
  await c.plugin(Runtime.default);await c.plugin(Completion.default);await c.plugin(Checkpoint.default,{every:3});await c.plugin(Termination.default);await c.plugin(Views.default);
  await c.plugin(Binding.default,{generation:'g'});
  await c.plugin(Runs.default,{maxPendingAcknowledgements:1});
  await c.plugin({inject:['drivers'],apply(ctx){ctx.drivers.register('summary',{async activate(intent){return {status:'completed',value:{reason:'success',requests:2,toolCalls:3,explorationErrors:1,actRecoverable:1,actFailed:0,requiredSatisfied:true},evidence:{reason:'success'},requiredOperations:[]}}})}});
  return {host,ctx:c};
}

row('D1 RunResult carries bounded activation summaries',async()=>{
  const env=await runFixture();
  try{
    const result=await env.ctx.runs.run(new AbortController().signal);
    assert.equal(result.status,'completed',JSON.stringify(result));
    assert.ok(result.activations,JSON.stringify(result));
    assert.equal(result.activations.truncated,true,JSON.stringify(result.activations));
    assert.equal(result.activations.entries.length,1,JSON.stringify(result.activations));
    assert.deepEqual(result.activations.entries[0],{actorId:'a',tick:'1',reason:'success',requests:2,toolCalls:3,explorationErrors:1,actRecoverable:1,actFailed:0,requiredSatisfied:true});
  }finally{await env.host.close()}
});
