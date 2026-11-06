import test from 'node:test';
import assert from 'node:assert/strict';
import {setup,request} from './pr07-support.mjs';
import {fixture} from './pr08-support.mjs';
import {PiActivation} from '../.build/core/dist/agent-runtime-pi/activation.js';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {extensions} from './pr08-support.mjs';
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
 let ctx;await run.host.ctx.plugin({inject:['actions','modelProviders','drivers','actorTools','threads','contextMount','prompts','database','perceptions','artifacts','attempts','agentRuntimes','slots'],apply(c){ctx=c;c.actions.register({name:'record_sum',effect:'world',inputSchema:{type:'object',properties:{a:{type:'number'}},required:['a'],additionalProperties:false},outputSchema:{},available:async()=>true,execute:async(input,context)=>{const operationId=context.operationId+'/sql';await context.transaction(request(operationId),async tx=>{await tx.execute({sql:'INSERT INTO h01_sums VALUES(?,?)',parameters:[operationId,1]})});return {operationId,status:'completed',value:{sum:1},original:null,artifacts:[],requiredOperations:[]}}});const provider=new ReplayProvider({driver:[assistant([{type:'toolCall',id:'bad\ud800id',name:'act',arguments:{action:'record_sum',input:{a:1}}}],'toolUse'),assistant([{type:'text',text:'done'}])]});c.modelProviders.register('replay/v1',provider)}});
 await run.host.ctx.plugin(DriverLlm,{name:'llm/v1',provider:'replay/v1',model:'driver',maxRequests:2,maxToolCalls:1});
 const driver=await ctx.drivers.acquire('llm/v1',run.context('h01-t11'));let result;try{result=await driver.value.activate({id:'t11',actorId:run.context('x').actorId,momentId:run.context('x').momentId,source:'test',driver:'llm/v1',input:'bad id'},run.context('h01-t11'))}finally{await driver.release()}
 assert.equal(result.status,'incomplete',JSON.stringify(result));assert.equal(result.value.reason,'provider_error',JSON.stringify(result));
 }finally{await run.close()}});

const high=String.fromCharCode(0xd800);
function textError(code,field){return error=>{assert.equal(error.code,code);assert.equal(error.field,field);return true}}
test('NEW-6-T12 thread open rejects malformed actor/moment before SQL',async()=>{const run=await fixture();let ctx,view;try{
 await run.host.ctx.plugin({inject:['threads','database'],apply(c){ctx=c}});
 console.error('T12 snapshot begin');
 view=await ctx.database.snapshot({maxAgeMs:30000});
 console.error('T12 snapshot ok');
 let error;try{await ctx.threads.open('a'+high,'m',view)}catch(cause){error=cause}
 console.error('T12 actor error',JSON.stringify({code:error?.code,field:error?.field,message:error?.message}));
 assert.ok(error,'expected malformed actor rejection');assert.equal(error.code,'THREAD_TEXT_NOT_WELL_FORMED');assert.equal(error.field,'actorId');
 error=undefined;try{await ctx.threads.open('a','m'+high,view)}catch(cause){error=cause}
 console.error('T12 moment error',JSON.stringify({code:error?.code,field:error?.field,message:error?.message}));
 assert.ok(error,'expected malformed moment rejection');assert.equal(error.code,'THREAD_TEXT_NOT_WELL_FORMED');assert.equal(error.field,'momentId');
 }finally{try{if(view){console.error('T12 view.close begin');await view.close();console.error('T12 view.close done')}}finally{console.error('T12 run.close begin');await run.close();console.error('T12 run.close done')}}});
test('NEW-6-T13 thread prepare rejects malformed message.id before staging',async()=>{const run=await fixture();let ctx;try{await run.host.ctx.plugin({inject:['threads','database'],apply(c){ctx=c}});await assert.rejects(ctx.threads.prepare({id:'bad'+high,interactionId:'i',role:'user',content:'x',originalProviderFields:null}),textError('THREAD_TEXT_NOT_WELL_FORMED','message.id'))}finally{await run.close()}});
test('NEW-6-T15 action failure reason is normalized while raw evidence stays readable',async()=>{const run=await fixture();try{run.ctx.actions.register({name:'bad.reason',effect:'external',inputSchema:{},outputSchema:{},available:async()=>true,execute:async()=>{throw Error('boom'+high+'reason')}});const context=run.context('h01-t15');await assert.rejects(run.ctx.actions.execute('bad.reason',null,context));const lineage=run.ctx.attempts.authority().lineage,probe=await run.ctx.attempts.probe({lineage,operationId:'action:'+context.operationId,attempt:0});assert.equal(probe.record.status,'unknown');assert.ok(!probe.record.reason.includes(high));assert.ok(probe.record.artifacts.length>=1,JSON.stringify(probe.record.artifacts));const raw=[];for(const ref of probe.record.artifacts){let text='';for await(const part of run.ctx.attempts.readArtifact(ref))text+=Buffer.from(part).toString();raw.push(text)}assert.ok(raw.some(text=>text.includes('\\ud800')),raw.join('|'))}finally{await run.close()}});

test('NEW-6-T16 storage persistence outranks provider invalid in localReason',()=>{const sink={limits:{maxPendingEvents:8,maxPendingBytes:4096},append:async()=>{},drain:async()=>{},appendUndispatched:async()=>{}};const empty={items:[]};const activation=new PiActivation({}, {runId:'r',operationId:'o',signal:new AbortController().signal},sink,{request:empty,context:empty,toolPolicy:empty,completion:empty,observer:empty},new AbortController(),()=>{});activation.toolFailure(Object.assign(Error('bad text'),{code:'INFERENCE_MODEL_TEXT_NOT_WELL_FORMED'}));assert.equal(activation.localReason(),'provider_error');activation.failSave(Error('ledger down'));assert.equal(activation.localReason(),'persistence_error');activation.failSave(Object.assign(Error('commit unknown'),{code:'SQLITE_COMMIT_UNKNOWN'}));assert.equal(activation.localReason(),'persistence_unknown')});

test('NEW-6-T17 invalid model tool plus real sink persistence failure stays persistence_error',async()=>{const run=await fixture();let provider,prepared,runtime;try{provider=new ReplayProvider({bad:[assistant([{type:'toolCall',id:'good',name:'bad',arguments:{}}],'toolUse'),assistant([{type:'text',text:'done'}])]});const context=run.context('h01-t17');prepared=await provider.prepare({provider:'replay/v1',model:'bad'},context);runtime=new PiRuntime();const sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async frame=>{if(frame.event.type==='tool_execution_end')throw Error('ledger down')},async()=>{},()=>{});const outcome=await runtime.run({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'x',timestamp:0}],tools:[{name:'bad',label:'bad',description:'bad',parameters:{},execute:async()=>{throw Object.assign(Error('model text'),{code:'INFERENCE_MODEL_TEXT_NOT_WELL_FORMED'})}}],actionReceipts:{read:async()=>[]},selection:prepared.value,budget:{maxRequests:2,maxToolCalls:1},requiredActions:[]},context,sink,extensions());assert.equal(outcome.reason,'persistence_error',JSON.stringify(outcome));await runtime.drain('done');runtime=undefined}finally{await prepared?.release().catch(()=>{});await provider?.close();await runtime?.drain('done').catch(()=>{});await run.close()}});
