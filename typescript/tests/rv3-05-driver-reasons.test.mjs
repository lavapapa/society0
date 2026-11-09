import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,rename,rm} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {setup,request,read} from './pr07-support.mjs';
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
import {ToolReceipts} from '../.build/core/dist/actor-tools/receipts.js';
import {INFERENCE_TOOL_POLICY} from '../.build/core/dist/contracts/inference.js';

const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
const assistant=(content,stopReason='stop')=>({role:'assistant',content,api:'openai-completions',provider:'replay',model:'driver',usage,stopReason,timestamp:1});
const toolTurn=(id,action,input)=>assistant([{type:'toolCall',id,name:'act',arguments:{action,input}}],'toolUse');
const finalTurn=assistant([{type:'text',text:'driver finished'}]);
const defaultScript=[toolTurn('sum','record_sum',{a:20,b:22}),finalTurn];

/** 真实感知源：一页一条inline原文；可替换collect制造故障。 */
function source(collect){
  return {id:'fixture/events',schema:{type:'object'},cursorScope:'actor',available:async()=>true,versions:async()=>[],
    collect:collect??(async()=>({items:[{id:'one',kind:'fixture',material:{kind:'inline',value:{text:'one percept'}},provenance:{fixture:true}}],nextCursor:'1',through:'1',hasMore:false}))};
}

/** 真实领域动作：world动作写driver_sums并产生回执；external动作声明外部效果。 */
function actionSet({storageFault=false,counts}={}){
  const record_sum={name:'record_sum',effect:'world',inputSchema:{type:'object',properties:{a:{type:'number'},b:{type:'number'}},required:['a','b']},outputSchema:{},available:async()=>true,
    execute:async(input,context)=>{
      if(counts)counts.sum++;
      if(storageFault)throw Object.assign(Error('storage commit unknown at action execute'),{code:'SQLITE_COMMIT_UNKNOWN'});
      const operationId=context.operationId+'/sql',value={sum:input.a+input.b};
      await context.transaction(request(operationId),async tx=>{await tx.execute({sql:'INSERT INTO driver_sums VALUES(?,?)',parameters:[operationId,value.sum]});return value});
      return {operationId,status:'completed',value,original:input,artifacts:[]};
    }};
  const external_probe={name:'external_probe',effect:'external',inputSchema:{type:'object'},outputSchema:{},available:async()=>true,
    execute:async()=>{throw Error('external effect result unknown')}};
  return [record_sum,external_probe];
}

async function fixture({perception,storageFault=false,script=defaultScript,driver={}}={}){
  const run=await setup({migrations:[actorToolsMigration({test:'rv3-05-driver'}),{owner:'driver-sum',from:0,to:1,after:[],source:{test:'rv3-05'},statements:[{sql:'CREATE TABLE driver_sums(id TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)'}]}]});
  const histories=[],counts={sum:0};
  try{
    for(const [plugin,config] of [[Prompts,{sections:[{id:'instructions',text:'Call the requested tools and preserve their results.'}],contexts:[{id:'frozen',text:'frozen context marker'}]}],[ContextMount,{}],[ExplorationShell,{}],[ActorTools,{}],[ModelProviders,{}],[AgentRuntimes,{}],[AgentRuntimePi,{}],[InferenceDefaults,{}],[DriverRegistry,{}]])await run.host.ctx.plugin(plugin,config);
    let ctx;
    await run.host.ctx.plugin({inject:['actions','modelProviders','drivers','actorTools','threads','contextMount','prompts','database','perceptions','artifacts','attempts','agentRuntimes','slots'],apply(c){ctx=c;
      c.perceptions.register(perception??source());
      for(const action of actionSet({storageFault,counts}))c.actions.register(action);
      const provider=new ReplayProvider({driver:script});
      const prepare=provider.prepare.bind(provider);
      provider.prepare=async(...args)=>{const selected=await prepare(...args),stream=selected.value.streamFn;return {value:{...selected.value,streamFn:(...input)=>{histories.push(structuredClone(input[1].messages));return stream(...input)}},release:()=>selected.release()}};
      c.modelProviders.register('replay/v1',provider);
    }});
    await run.host.ctx.plugin(DriverLlm,{name:'llm/v1',provider:'replay/v1',model:'driver',perceptions:['fixture/events'],requiredActions:[{name:'record_sum',minimumCompleted:1}],maxRequests:3,maxToolCalls:1,...driver});
    const base=dirname(run.directory),guard=join(tmpdir(),'pr07-chain-');
    const close=async()=>{try{await run.close()}finally{if(base.startsWith(guard))await rm(base,{recursive:true,force:true})}};
    return {run,ctx,histories,counts,close};
  }catch(error){await run.close();throw error}
}

/** 只替换服务实例方法：包装ctx.database.transaction，把body收到的tx换成execute可故障的代理。 */
function transactionProxy(env,suffix,makeBody){
  const original=env.ctx.database.transaction.bind(env.ctx.database);let fired=false;
  env.ctx.database.transaction=(lease,request_,body)=>{
    if(fired||!request_.operationId.endsWith(suffix))return original(lease,request_,body);
    fired=true;return makeBody(original,lease,request_,body);
  };
  return ()=>fired;
}

function executeFault(code){
  return (original,lease,request_,body)=>original(lease,request_,tx=>{
    let calls=0;
    const proxy={...tx,execute:async statement=>{if(++calls===1)throw Object.assign(Error(code+': injected statement fault'),{code});return tx.execute(statement)}};
    return body(proxy);
  });
}

/** B簇T4方法：暂存真实COMMIT响应，再terminate真实worker，得到真实的提交未知。 */
function interceptCommit(worker){
  const originalPost=worker.postMessage.bind(worker),listeners=worker.listeners('message');
  const commit=Promise.withResolvers(),response=Promise.withResolvers();let commitId,stashed=false;
  const onMessage=message=>{if(commitId!==undefined&&message.id===commitId&&!stashed){stashed=true;response.resolve(message);return}for(const listener of listeners)listener(message)};
  worker.postMessage=(message,...rest)=>{if(message.op==='commit'&&commitId===undefined){commitId=message.id;commit.resolve()}return originalPost(message,...rest)};
  worker.removeAllListeners('message');worker.on('message',onMessage);
  return {commit:commit.promise,response:response.promise,restore(){worker.postMessage=originalPost;worker.removeAllListeners('message');for(const listener of listeners)worker.on('message',listener)}};
}

/** 真实ledger故障：终止已连接的attempts worker并暂移authority数据库，让claim走真实unknown分支（不伪造status）。 */
async function breakLedger(env){
  const directory=env.ctx.attempts.authority().directory,database=join(directory,'attempts.sqlite');
  const channel=env.ctx.attempts.ledger.channel;
  if(channel&&!channel.dead)await channel.worker.terminate();
  await rename(database,database+'.claim-fault');
  return async()=>{await rename(database+'.claim-fault',database)};
}

/** 回执端口的一次性实例替换：直接替换类原型方法，避免破坏服务调用方的traceable上下文。 */
function patchReceipts(method,replacement){
  const original=ToolReceipts.prototype[method];
  ToolReceipts.prototype[method]=replacement;
  return ()=>{ToolReceipts.prototype[method]=original};
}

async function activate(env,operationId,{signal}={}){
  const context=env.run.context(operationId),effective=signal?AbortSignal.any([context.signal,signal]):context.signal;
  const intent={id:operationId,actorId:context.actorId,momentId:context.momentId,source:'test',driver:'llm/v1',input:'rv3-05 '+operationId};
  const driver=await env.ctx.drivers.acquire('llm/v1',context);
  try{return await driver.value.activate(intent,{...context,signal:effective})}finally{await driver.release()}
}

function expectIncomplete(result,reason,stage,code){
  assert.equal(result.status,'incomplete',JSON.stringify(result));
  assert.equal(result.value.reason,reason,JSON.stringify(result.value));
  assert.equal(result.evidence.stage,stage,JSON.stringify(result.evidence));
  assert.notEqual(result.evidence.stage,'unclassified');
  assert.ok(result.evidence.error,'evidence keeps the cause text');
  if(code!==undefined)assert.equal(result.evidence.code,code,JSON.stringify(result.evidence));
}

function expectToolReason(result,reason){
  assert.equal(result.status,'incomplete',JSON.stringify(result));
  assert.equal(result.value.reason,reason,JSON.stringify(result.value));
  assert.equal(result.evidence.reason,reason,JSON.stringify(result.evidence));
}

const row=(name,body)=>test(name,{timeout:900000},body);

async function scenario(options,body){
  const env=await fixture(options);
  try{return await body(env)}finally{await env.close()}
}

row('RV3-05 baseline completed before any injection',async()=>{
  await scenario({},async env=>{
    const result=await activate(env,'rv305/baseline');
    assert.equal(result.status,'completed',JSON.stringify(result));
    assert.equal(result.value.reason,'success',JSON.stringify(result.value));
    assert.equal(result.evidence.actionReceipts[0].action,'record_sum');
    assert.equal(result.evidence.actionReceipts[0].receipt.value.sum,42);
    assert.ok(env.histories.length>=1);
    for(const id of result.requiredOperations)assert.equal((await env.ctx.database.probe(id)).status,'committed');
    console.log(JSON.stringify({case:'RV3-05-baseline',status:result.status,reason:result.value.reason,requiredOperations:result.requiredOperations.length,sum:result.evidence.actionReceipts[0].receipt.value.sum}));
  });
});

row('RV3-05-T01 1 thread.page content invariant stays thread_error/history',async()=>{
  await scenario({},async env=>{
    const original=env.ctx.threads.page.bind(env.ctx.threads);let fired=false;
    env.ctx.threads.page=(...args)=>{if(!fired){fired=true;throw Error('disk says INFERENCE_CONTEXT_LIMIT')}return original(...args)};
    const result=await activate(env,'rv305/1');
    expectIncomplete(result,'thread_error','thread.history');
    assert.equal(result.evidence.code,'LLM_THREAD_ERROR');
    assert.match(String(result.evidence.error),/INFERENCE_CONTEXT_LIMIT/);
    console.log(JSON.stringify({case:'RV3-05-T01-1',reason:result.value.reason,stage:result.evidence.stage,code:result.evidence.code}));
  });
});

row('RV3-05-T01 1b withView keeps main error when close also fails',async()=>{
  await scenario({},async env=>{
    const original=env.ctx.database.snapshot.bind(env.ctx.database);
    env.ctx.database.snapshot=async options=>{const view=await original(options);return {...view,close:async()=>{await view.close();throw Object.assign(Error('SNAPSHOT_CLOSE_EIO'),{code:'SNAPSHOT_CLOSE_EIO'})}}};
    env.ctx.threads.open=async()=>{throw Object.assign(Error('THREAD_OPEN_IO'),{code:'THREAD_OPEN_IO'})};
    const result=await activate(env,'rv305/1b');
    expectIncomplete(result,'thread_error','thread.open','THREAD_OPEN_IO');
    assert.match(String(result.evidence.error),/THREAD_OPEN_IO/);
    assert.ok(!String(result.evidence.error).includes('SNAPSHOT_CLOSE_EIO'),'close失败不得覆盖主体错误');
    console.log(JSON.stringify({case:'RV3-05-T01-1b',reason:result.value.reason,stage:result.evidence.stage,code:result.evidence.code,closeOverrode:false}));
  });
});

row('RV3-05-T01 2 appendInput statement fault is thread_error/thread.appendInput after real rollback',async()=>{
  await scenario({},async env=>{
    transactionProxy(env,'/input',executeFault('SQLITE_IOERR'));
    const result=await activate(env,'rv305/2');
    expectIncomplete(result,'thread_error','thread.appendInput','SQLITE_IOERR');
    assert.equal((await env.ctx.database.probe('rv305/2/input')).status,'not-committed');
    console.log(JSON.stringify({case:'RV3-05-T01-2',reason:result.value.reason,stage:result.evidence.stage,probe:(await env.ctx.database.probe('rv305/2/input')).status}));
  });
});

row('RV3-05-T01 3 perception commit statement fault is perception_error/perception.commit after real rollback',async()=>{
  await scenario({},async env=>{
    transactionProxy(env,'/perception/0',executeFault('SQLITE_IOERR'));
    const result=await activate(env,'rv305/3');
    expectIncomplete(result,'perception_error','perception.commit','SQLITE_IOERR');
    assert.equal((await env.ctx.database.probe('rv305/3/perception/0')).status,'not-committed');
    console.log(JSON.stringify({case:'RV3-05-T01-3',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 4 perception collect fault is perception_error/perception.collect',async()=>{
  const perception=source(async()=>{throw Object.assign(Error('perception source EIO'),{code:'PERCEPTION_COLLECT_IO'})});
  await scenario({perception},async env=>{
    const result=await activate(env,'rv305/4');
    expectIncomplete(result,'perception_error','perception.collect','PERCEPTION_COLLECT_IO');
    console.log(JSON.stringify({case:'RV3-05-T01-4',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 5 perception material read fault is perception_error/perception.material',async()=>{
  const perception=source(async()=>({items:[{id:'one',kind:'fixture',material:{kind:'reference',ref:{object:'missing-object',rawBytes:'16',mediaType:'application/json'}},provenance:{fixture:true}}],nextCursor:'1',through:'1',hasMore:false}));
  await scenario({perception},async env=>{
    env.ctx.threads.readPayload=()=>{throw Object.assign(Error('material read EIO'),{code:'EIO'})};
    const result=await activate(env,'rv305/5');
    expectIncomplete(result,'perception_error','perception.material','EIO');
    console.log(JSON.stringify({case:'RV3-05-T01-5',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 6 prompts acquire fault is setup_error/prompts',async()=>{
  await scenario({},async env=>{
    env.ctx.prompts.acquire=async()=>{throw Object.assign(Error('prompt assembly unavailable'),{code:'PROMPTS_UNAVAILABLE'})};
    const result=await activate(env,'rv305/6');
    expectIncomplete(result,'setup_error','prompts','PROMPTS_UNAVAILABLE');
    console.log(JSON.stringify({case:'RV3-05-T01-6',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 7 model prepare fault is provider_error/model.prepare',async()=>{
  await scenario({},async env=>{
    env.ctx.modelProviders.prepare=async()=>{throw Object.assign(Error('MODEL_UNAVAILABLE'),{code:'MODEL_UNAVAILABLE'})};
    const result=await activate(env,'rv305/7');
    expectIncomplete(result,'provider_error','model.prepare','MODEL_UNAVAILABLE');
    console.log(JSON.stringify({case:'RV3-05-T01-7',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 8 history byte budget is context_limit/history',async()=>{
  await scenario({driver:{maxHistoryBytes:1}},async env=>{
    const result=await activate(env,'rv305/8');
    expectIncomplete(result,'context_limit','history','INFERENCE_CONTEXT_LIMIT');
    console.log(JSON.stringify({case:'RV3-05-T01-8',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 9 aborted signal propagated through page reason is cancelled',async()=>{
  await scenario({},async env=>{
    const controller=new AbortController(),reason=Error('row9 cancellation');
    const original=env.ctx.threads.page.bind(env.ctx.threads);let fired=false;
    env.ctx.threads.page=(...args)=>{if(!fired){fired=true;controller.abort(reason);throw reason}return original(...args)};
    const result=await activate(env,'rv305/9',{signal:controller.signal});
    expectIncomplete(result,'cancelled','thread.history');
    assert.match(String(result.evidence.error),/row9 cancellation/);
    console.log(JSON.stringify({case:'RV3-05-T01-9',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 10 real commit unknown after abort stays persistence_unknown',async()=>{
  await scenario({},async env=>{
    const controller=new AbortController(),reason=Error('row10 cancellation'),worker=env.ctx.database.database.rpc.worker;
    let restore=()=>{};
    try{
      transactionProxy(env,'/input',(original,lease,request_,body)=>{
        controller.abort(reason);
        const held=interceptCommit(worker);restore=held.restore;
        const pending=original(lease,request_,body);pending.catch(()=>{});
        return (async()=>{await held.commit;await held.response;await worker.terminate();return pending})();
      });
      const result=await activate(env,'rv305/10',{signal:controller.signal});
      expectIncomplete(result,'persistence_unknown','transaction:input','SQLITE_COMMIT_UNKNOWN');
      assert.equal(result.evidence.committed,null,JSON.stringify(result.evidence));
      assert.equal(result.evidence.receipt,null,JSON.stringify(result.evidence));
      await new Promise(resolve=>setImmediate(resolve));
      const probe=await env.ctx.database.probe('rv305/10/input');
      assert.equal(probe.status,'committed',JSON.stringify(probe));
      assert.equal(probe.receipt.operationId,'rv305/10/input');
      console.log(JSON.stringify({case:'RV3-05-T01-10',reason:result.value.reason,stage:result.evidence.stage,probe:probe.status}));
    }finally{restore()}
  });
});

row('RV3-05-T01 11 real segment failure keeps committed receipt out of requiredOperations',async()=>{
  await scenario({},async env=>{
    transactionProxy(env,'/input',async(original,lease,request_,body)=>{
      const revision=await env.ctx.database.readRevision();
      await mkdir(join(env.run.directory,'segments',String(BigInt(revision)+1n)+'.changeset'),{recursive:true});
      return original(lease,request_,body);
    });
    const result=await activate(env,'rv305/11');
    expectIncomplete(result,'persistence_unknown','transaction:input','SQLITE_SEGMENT_FAILED');
    assert.equal(result.evidence.committed,true,JSON.stringify(result.evidence));
    assert.equal(result.evidence.receipt.operationId,'rv305/11/input',JSON.stringify(result.evidence.receipt));
    assert.ok(!result.requiredOperations.includes('rv305/11/input'),JSON.stringify(result.requiredOperations));
    const probe=await env.ctx.database.probe('rv305/11/input');
    assert.equal(probe.status,'committed',JSON.stringify(probe));
    assert.equal(probe.receipt.revision,result.evidence.receipt.revision);
    console.log(JSON.stringify({case:'RV3-05-T01-11',reason:result.value.reason,committed:result.evidence.committed,receipt:result.evidence.receipt,requiredOperations:result.requiredOperations}));
  });
});

row('RV3-05-T01 12 undispatched claim fault is persistence_error/attempts.undispatched',async()=>{
  await scenario({},async env=>{
    const controller=new AbortController(),reason=Error('row12 cancellation');
    await env.run.host.ctx.plugin({inject:['slots'],apply(c){c.slots.contribute(INFERENCE_TOOL_POLICY,{id:'rv305/cancel',value:{before:async()=>{controller.abort(reason);return {block:true,terminate:true,reason:'rv305-cancel'}}}})}});
    const original=env.ctx.attempts.claim.bind(env.ctx.attempts);let fired=false;
    env.ctx.attempts.claim=async(...args)=>{if(!fired){fired=true;throw Object.assign(Error('attempt ledger unavailable'),{code:'LEDGER_IO'})}return original(...args)};
    const result=await activate(env,'rv305/12',{signal:controller.signal});
    expectIncomplete(result,'persistence_error','attempts.undispatched','LEDGER_IO');
    assert.equal(fired,true);
    assert.match(String(result.evidence.error),/attempt ledger unavailable/);
    console.log(JSON.stringify({case:'RV3-05-T01-12',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 12b undispatched claim unknown is persistence_error/attempts.undispatched',async()=>{
  await scenario({},async env=>{
    const controller=new AbortController(),reason=Error('row12b cancellation');
    let restoreLedger;
    await env.run.host.ctx.plugin({inject:['slots'],apply(c){c.slots.contribute(INFERENCE_TOOL_POLICY,{id:'rv305/ledger-fault',value:{before:async()=>{
      controller.abort(reason);
      restoreLedger=await breakLedger(env);
      return {block:true,terminate:true,reason:'rv305-ledger-fault'};
    }}})}});
    try{
      const result=await activate(env,'rv305/12b',{signal:controller.signal});
      expectIncomplete(result,'persistence_error','attempts.undispatched','ATTEMPT_LEDGER_UNKNOWN');
      assert.match(String(result.evidence.error),/ATTEMPT_AUTHORITY_UNAVAILABLE/);
      assert.equal(result.evidence.receipt,null);
      await restoreLedger();restoreLedger=undefined;
      const identity={lineage:env.ctx.attempts.authority().lineage,operationId:'rv305/12b/undispatched/sum',attempt:0};
      const probe=await env.ctx.attempts.probe(identity);
      assert.equal(probe.status,'absent',JSON.stringify(probe));
      console.log(JSON.stringify({case:'RV3-05-T01-12b',reason:result.value.reason,stage:result.evidence.stage,code:result.evidence.code,probe:probe.status}));
    }finally{if(restoreLedger)await restoreLedger().catch(()=>{})}
  });
});

row('RV3-05-T01 12c undispatched existing pending is persistence_error/attempts.undispatched',async()=>{
  await scenario({},async env=>{
    const controller=new AbortController(),reason=Error('row12c cancellation');
    const lineage=env.ctx.attempts.authority().lineage;
    const identity={lineage,operationId:'rv305/12c/undispatched/sum',attempt:0};
    assert.equal((await env.ctx.attempts.claim(identity,{kind:'not_dispatched',threadId:'seed'})).status,'send-permitted');
    await env.run.host.ctx.plugin({inject:['slots'],apply(c){c.slots.contribute(INFERENCE_TOOL_POLICY,{id:'rv305/cancel-pending',value:{before:async()=>{controller.abort(reason);return {block:true,terminate:true,reason:'rv305-cancel-pending'}}}})}});
    const result=await activate(env,'rv305/12c',{signal:controller.signal});
    expectIncomplete(result,'persistence_error','attempts.undispatched','ATTEMPT_LEDGER_INCOMPLETE');
    assert.match(String(result.evidence.error),/pending/);
    assert.equal(result.evidence.receipt,null);
    const probe=await env.ctx.attempts.probe(identity);
    assert.equal(probe.status,'found',JSON.stringify(probe));
    assert.equal(probe.record.status,'pending',JSON.stringify(probe.record));
    assert.equal(probe.record.request.threadId,'seed','不得覆盖existing ledger记录');
    console.log(JSON.stringify({case:'RV3-05-T01-12c',reason:result.value.reason,stage:result.evidence.stage,code:result.evidence.code,existing:probe.record.status}));
  });
});

row('RV3-05-T01 12d undispatched existing settled stays durable without rewrite',async()=>{
  await scenario({},async env=>{
    const controller=new AbortController(),reason=Error('row12d cancellation');
    const lineage=env.ctx.attempts.authority().lineage;
    const identity={lineage,operationId:'rv305/12d/undispatched/sum',attempt:0};
    assert.equal((await env.ctx.attempts.claim(identity,{kind:'not_dispatched',threadId:'seed'})).status,'send-permitted');
    await env.ctx.attempts.settle(identity,{status:'not_dispatched',receipt:{toolCallId:'sum',toolName:'act',status:'not_dispatched',reason:'cancelled'}});
    const before=await env.ctx.attempts.probe(identity);
    await env.run.host.ctx.plugin({inject:['slots'],apply(c){c.slots.contribute(INFERENCE_TOOL_POLICY,{id:'rv305/cancel-settled',value:{before:async()=>{controller.abort(reason);return {block:true,terminate:true,reason:'rv305-cancel-settled'}}}})}});
    const result=await activate(env,'rv305/12d',{signal:controller.signal});
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'cancelled',JSON.stringify(result.value));
    assert.equal(result.evidence.reason,'cancelled');
    const after=await env.ctx.attempts.probe(identity);
    assert.equal(after.status,'found',JSON.stringify(after));
    assert.equal(after.record.status,'settled',JSON.stringify(after.record));
    assert.equal(after.record.physicalRequestId,before.record.physicalRequestId,'不得重写已settled事实');
    assert.equal(after.record.result.receipt.toolCallId,'sum');
    console.log(JSON.stringify({case:'RV3-05-T01-12d',reason:result.value.reason,existing:after.record.status,rewritten:false}));
  });
});

row('RV3-05-T01 13 action receipt reader fault is persistence_error/actions.receipts',async()=>{
  await scenario({},async env=>{
    const restore=patchReceipts('read',async()=>{throw Object.assign(Error('receipts unavailable'),{code:'RECEIPTS_IO'})});
    try{
      const result=await activate(env,'rv305/13');
      expectIncomplete(result,'persistence_error','actions.receipts','RECEIPTS_IO');
      assert.match(String(result.evidence.error),/receipts unavailable/);
      console.log(JSON.stringify({case:'RV3-05-T01-13',reason:result.value.reason,stage:result.evidence.stage}));
    }finally{restore()}
  });
});

row('RV3-05-T01 14 snapshot close fault after successful body is persistence_error/db.close',async()=>{
  await scenario({},async env=>{
    const original=env.ctx.database.snapshot.bind(env.ctx.database);let fired=false;
    env.ctx.database.snapshot=async options=>{const view=await original(options);if(fired)return view;fired=true;return {...view,close:async()=>{await view.close();throw Object.assign(Error('SNAPSHOT_CLOSE_EIO'),{code:'SNAPSHOT_CLOSE_EIO'})}}};
    const result=await activate(env,'rv305/14');
    expectIncomplete(result,'persistence_error','db.close','SNAPSHOT_CLOSE_EIO');
    console.log(JSON.stringify({case:'RV3-05-T01-14',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T01 18 transaction outcome missing is internal_error',async()=>{
  await scenario({},async env=>{
    transactionProxy(env,'/input',()=>{throw Object.assign(Error('internal invariant: physical settled without outcome'),{code:'SQLITE_TX_OUTCOME_MISSING'})});
    const result=await activate(env,'rv305/18');
    expectIncomplete(result,'internal_error','transaction:input','SQLITE_TX_OUTCOME_MISSING');
    const contract=await import('../.build/core/dist/driver-llm/index.js');
    assert.equal(typeof contract.LlmActivationError,'function','driver-llm exports LlmActivationError');
    console.log(JSON.stringify({case:'RV3-05-T01-18',reason:result.value.reason,stage:result.evidence.stage}));
  });
});

row('RV3-05-T02 15 receipt save commit unknown is persistence_unknown',async()=>{
  await scenario({},async env=>{
    const restore=patchReceipts('save',async()=>{throw Object.assign(Error('receipt save commit unknown'),{code:'SQLITE_COMMIT_UNKNOWN'})});
    try{
      const result=await activate(env,'rv305/15');
      expectToolReason(result,'persistence_unknown');
      assert.equal(result.evidence.requests,1,JSON.stringify(result.evidence));
      assert.equal(result.evidence.actionReceipts.length,0);
      assert.equal((await env.ctx.database.probe('rv305/15/tool/sum/sql')).status,'committed');
      const identity={lineage:env.ctx.attempts.authority().lineage,operationId:'action-run:'+JSON.stringify(['pr07','rv305/15/tool/sum']),attempt:0};
      const probe=await env.ctx.attempts.probe(identity);
      assert.equal(probe.status,'found',JSON.stringify(probe));
      assert.equal(probe.record.status,'settled',JSON.stringify(probe.record));
      assert.equal(probe.record.result.status,'returned',JSON.stringify(probe.record.result));
      assert.match(JSON.stringify(probe.record.result),/"sum":42/);
      console.log(JSON.stringify({case:'RV3-05-T02-15',reason:result.value.reason,requests:result.evidence.requests,receipts:result.evidence.actionReceipts.length,attempt:probe.record.status,returned:probe.record.result.status}));
    }finally{restore()}
  });
});

row('RV3-05-T02 16 action execute storage unknown is persistence_unknown not transport_unknown',async()=>{
  await scenario({storageFault:true},async env=>{
    const result=await activate(env,'rv305/16');
    expectToolReason(result,'persistence_unknown');
    assert.equal(result.evidence.requests,1,JSON.stringify(result.evidence));
    const receipt=result.evidence.actionReceipts[0].receipt;
    assert.equal(receipt.value.code,'SQLITE_COMMIT_UNKNOWN');
    assert.equal(receipt.value.persistenceUnknown,true);
    assert.equal(receipt.value.externalUnknown,false);
    const identity={lineage:env.ctx.attempts.authority().lineage,operationId:'action-run:'+JSON.stringify(['pr07','rv305/16/tool/sum']),attempt:0};
    const probe=await env.ctx.attempts.probe(identity);
    assert.equal(probe.status,'found',JSON.stringify(probe));
    assert.equal(probe.record.status,'unknown',JSON.stringify(probe.record));
    assert.equal(probe.record.result.code,'SQLITE_COMMIT_UNKNOWN');
    console.log(JSON.stringify({case:'RV3-05-T02-16',reason:result.value.reason,code:receipt.value.code,persistenceUnknown:receipt.value.persistenceUnknown,attempt:probe.record.status}));
  });
});

row('RV3-05-T02 17 receipt save segment failure is persistence_unknown',async()=>{
  await scenario({},async env=>{
    const restore=patchReceipts('save',async()=>{throw Object.assign(Error('receipt save segment failure'),{code:'SQLITE_SEGMENT_FAILED'})});
    try{
      const result=await activate(env,'rv305/17');
      expectToolReason(result,'persistence_unknown');
      assert.equal(result.evidence.requests,1,JSON.stringify(result.evidence));
      assert.equal(result.evidence.actionReceipts.length,0);
      assert.equal((await env.ctx.database.probe('rv305/17/tool/sum/sql')).status,'committed');
      const identity={lineage:env.ctx.attempts.authority().lineage,operationId:'action-run:'+JSON.stringify(['pr07','rv305/17/tool/sum']),attempt:0};
      const probe=await env.ctx.attempts.probe(identity);
      assert.equal(probe.status,'found',JSON.stringify(probe));
      assert.equal(probe.record.status,'settled',JSON.stringify(probe.record));
      assert.equal(probe.record.result.status,'returned',JSON.stringify(probe.record.result));
      assert.match(JSON.stringify(probe.record.result),/"sum":42/);
      console.log(JSON.stringify({case:'RV3-05-T02-17',reason:result.value.reason,requests:result.evidence.requests,attempt:probe.record.status,returned:probe.record.result.status}));
    }finally{restore()}
  });
});

row('RV3-05-T02 19 external action attempt unknown stays transport_unknown',async()=>{
  const script=[toolTurn('external-call','external_probe',{}),finalTurn];
  await scenario({script},async env=>{
    const result=await activate(env,'rv305/19');
    expectToolReason(result,'transport_unknown');
    assert.equal(result.evidence.requests,1,JSON.stringify(result.evidence));
    const receipt=result.evidence.actionReceipts[0].receipt;
    assert.equal(receipt.value.code,'ACTION_ATTEMPT_UNKNOWN');
    assert.equal(receipt.value.externalUnknown,true);
    assert.equal(receipt.value.persistenceUnknown,false);
    console.log(JSON.stringify({case:'RV3-05-T02-19',reason:result.value.reason,code:receipt.value.code,externalUnknown:receipt.value.externalUnknown}));
  });
});

row('RV3-05-T02 19b action claim unavailable is persistence_unknown with storage receipt',async()=>{
  const script=[toolTurn('storage-claim','record_sum',{a:20,b:22}),finalTurn];
  await scenario({script},async env=>{
    let restoreLedger;
    await env.run.host.ctx.plugin({inject:['slots'],apply(c){c.slots.contribute(INFERENCE_TOOL_POLICY,{id:'rv305/ledger-claim-fault',value:{before:async()=>{restoreLedger=await breakLedger(env)}}})}});
    try{
      const result=await activate(env,'rv305/19b');
      expectToolReason(result,'persistence_unknown');
      assert.equal(result.evidence.requests,1,JSON.stringify(result.evidence));
      assert.equal(env.counts.sum,0,'claim失败时动作回调不得执行');
      const receipt=result.evidence.actionReceipts[0].receipt;
      assert.equal(receipt.value.code,'ACTION_ATTEMPT_UNKNOWN');
      assert.equal(receipt.value.persistenceUnknown,true);
      assert.equal(receipt.value.externalUnknown,false);
      await restoreLedger();restoreLedger=undefined;
      const identity={lineage:env.ctx.attempts.authority().lineage,operationId:'action-run:'+JSON.stringify(['pr07','rv305/19b/tool/storage-claim']),attempt:0};
      const probe=await env.ctx.attempts.probe(identity);
      assert.equal(probe.status,'absent',JSON.stringify(probe));
      console.log(JSON.stringify({case:'RV3-05-T02-19b',reason:result.value.reason,code:receipt.value.code,persistenceUnknown:receipt.value.persistenceUnknown,externalUnknown:receipt.value.externalUnknown,executed:env.counts.sum,attempt:probe.status}));
    }finally{if(restoreLedger)await restoreLedger().catch(()=>{})}
  });
});

test('RV3-05 release failures keep unknown reasons and downgrade success',{timeout:900000},async()=>{
  const releaseFault=async env=>{
    const original=env.ctx.agentRuntimes.acquire.bind(env.ctx.agentRuntimes);
    env.ctx.agentRuntimes.acquire=async(...args)=>{const held=await original(...args);return {value:held.value,release:async()=>{await held.release();throw Object.assign(Error('RUNTIME_RELEASE_FAILED'),{code:'RUNTIME_RELEASE_FAILED'})}}};
  };
  await scenario({},async env=>{
    await releaseFault(env);
    const result=await activate(env,'rv305/release-success');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'persistence_error');
    assert.equal(result.evidence.activation.reason,'success');
    assert.match(JSON.stringify(result.evidence.releaseFailures),/RUNTIME_RELEASE_FAILED/);
  });
  await scenario({storageFault:true},async env=>{
    await releaseFault(env);
    const result=await activate(env,'rv305/release-storage');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'persistence_unknown',JSON.stringify(result));
    assert.equal(result.evidence.activation.reason,'persistence_unknown');
    assert.match(JSON.stringify(result.evidence.releaseFailures),/RUNTIME_RELEASE_FAILED/);
  });
  const script=[toolTurn('external-release','external_probe',{}),finalTurn];
  await scenario({script},async env=>{
    await releaseFault(env);
    const result=await activate(env,'rv305/release-external');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'transport_unknown',JSON.stringify(result));
    assert.equal(result.evidence.activation.reason,'transport_unknown');
    assert.match(JSON.stringify(result.evidence.releaseFailures),/RUNTIME_RELEASE_FAILED/);
  });
});

test('RV3-05 unresolved orphan tool call stays thread_error/thread.history',{timeout:900000},async()=>{
  await scenario({},async env=>{
    const context=env.run.context('rv305/orphan-append');
    const opened=await read(env.ctx,view=>env.ctx.threads.open(context.actorId,context.momentId,view));
    const orphan=await env.ctx.threads.prepare({id:'rv305-orphan',interactionId:opened.interactionId,role:'assistant',content:assistant([{type:'toolCall',id:'orphan-call',name:'read',arguments:{path:'/workspace/missing'}}],'toolUse'),originalProviderFields:null},context.signal);
    await context.database.transaction(context.writeLease,{...request('rv305/orphan-append'),maxStatements:32,maxChangedRows:64},async tx=>{await env.ctx.threads.append(tx,opened.id,orphan)});
    const result=await activate(env,'rv305/orphan');
    expectIncomplete(result,'thread_error','thread.history','INFERENCE_UNRESOLVED_TOOL');
  });
  await scenario({},async env=>{
    const context=env.run.context('rv305/orphan-pending-append');
    const opened=await read(env.ctx,view=>env.ctx.threads.open(context.actorId,context.momentId,view));
    const orphan=await env.ctx.threads.prepare({id:'rv305-orphan-pending',interactionId:opened.interactionId,role:'assistant',content:assistant([{type:'toolCall',id:'pending-call',name:'read',arguments:{path:'/workspace/missing'}}],'toolUse'),originalProviderFields:{activationId:'rv305/orphan-pending'}},context.signal);
    await context.database.transaction(context.writeLease,{...request('rv305/orphan-pending-append'),maxStatements:32,maxChangedRows:64},async tx=>{await env.ctx.threads.append(tx,opened.id,orphan)});
    const identity={lineage:env.ctx.attempts.authority().lineage,operationId:'rv305/orphan-pending/undispatched/pending-call',attempt:0};
    assert.equal((await env.ctx.attempts.claim(identity,{kind:'not_dispatched',threadId:opened.id})).status,'send-permitted');
    const result=await activate(env,'rv305/orphan-pending-read');
    expectIncomplete(result,'thread_error','thread.history','INFERENCE_UNRESOLVED_TOOL');
  });
});

test('RV3-05 unknown action is a recoverable rejection and required action stays unsatisfied',{timeout:900000},async()=>{
  const script=[toolTurn('ghost','ghost_action',{}),finalTurn,finalTurn];
  await scenario({script},async env=>{
    const result=await activate(env,'rv305/unknown-action');
    assert.equal(result.status,'incomplete',JSON.stringify(result));
    assert.equal(result.value.reason,'required_action_failed',JSON.stringify(result));
    assert.equal(result.value.actRecoverable,1,JSON.stringify(result.value));
    const receipt=result.evidence.actionReceipts[0].receipt;
    assert.equal(receipt.status,'rejected');
    assert.equal(receipt.value.code,'ACTION_UNAVAILABLE');
    for(const id of receipt.requiredOperations)assert.equal((await env.ctx.database.probe(id)).status,'committed');
  });
});
