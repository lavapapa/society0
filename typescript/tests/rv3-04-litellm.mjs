import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdir,open} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';

const arg=name=>{const index=process.argv.indexOf(name);return index>0?process.argv[index+1]:undefined};
const evidenceDir=arg('--evidence-dir'),gatewayEnv=arg('--gateway-env');
let evidence,secret;
async function save(name,value){const file=await open(new URL(name,evidence),'w');try{await file.writeFile(JSON.stringify(value,null,2));await file.sync()}finally{await file.close()}if(secret)assert.ok(!(await readFile(new URL(name,evidence),'utf8')).includes(secret),'TOKEN_LEAK '+name)}

const cost={input:0,output:0,cacheRead:0,cacheWrite:0};
const base={name:'Authorized local Qwen',api:'openai-completions',provider:'local-gateway',baseUrl:'http://127.0.0.1:4000/v1',reasoning:true,input:['text'],cost,contextWindow:8192,maxTokens:256,compat:{supportsStore:false,supportsDeveloperRole:false,supportsReasoningEffort:false,maxTokensField:'max_tokens',thinkingFormat:'qwen-chat-template'}};
const models=[{...base,id:'Qwen3.6-35B-A3B-FP8'},{...base,id:'does-not-exist',name:'Missing model fixture'}];

async function exercise(run,provider,modelId,name){
  const context=run.context(name);
  let selected;
  try {
    selected=await provider.prepare({provider:'pi/v1',model:modelId,reasoning:'off'},context);
    try {
      const stream=await selected.value.streamFn(selected.value.model,{messages:[{role:'user',content:'Reply with the single word OK.',timestamp:Date.now()}]},{});
      for await (const _ of stream) {}
    } catch {}
    await selected.value.drain();
    const settlements=[...(selected.value.attemptSettlements??[])].map(item=>({status:item.status,httpStatus:item.httpStatus,cause:item.cause}));
    const probe=await run.ctx.attempts.probe({lineage:run.ctx.attempts.authority().lineage,operationId:context.operationId+'/model/1',attempt:0});
    const record=probe.status==='found'?probe.record:undefined;
    return {model:modelId,settlements,probe:probe.status,recordStatus:record?.status??null,reason:record?.reason??null,result:record?.result??null};
  } finally {
    await selected?.release().catch(()=>{});
  }
}

test('RV3-04-T05 authorized local gateway classifies complete 4xx and success',{skip:!process.argv.includes('--allow-local-model')},async()=>{
  if(!evidenceDir||!gatewayEnv)throw Error('RV3_04_LIVE_ARGS --evidence-dir and --gateway-env are required');
  evidence=pathToFileURL(evidenceDir+'/');await mkdir(evidence,{recursive:true});
  const run=await fixture();const facts=[];
  const credentials=new InMemoryCredentialStore();
  const line=(await readFile(gatewayEnv,'utf8')).split(/\r?\n/).find(item=>item.startsWith('LITELLM_MASTER_KEY='));
  assert.ok(line,'authorized gateway token missing');
  secret=line.slice('LITELLM_MASTER_KEY='.length).trim();
  await credentials.modify('local-gateway',async()=>({type:'api_key',key:secret}));
  const modelSet=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  modelSet.setProvider(createProvider({id:'local-gateway',models,auth:{apiKey:envApiKeyAuth('explicit credential',[])},api:completions}));
  const provider=new PiModelProvider(modelSet,run.ctx.attempts,{runId:'pr07',providerId:'local-gateway',maxEventBytes:1048576,maxRawBytes:16777216,maxRequestBytes:16777216,options:{reasoning:'off',maxTokens:256,temperature:0,maxRetries:0,transport:'sse'},timeoutMs:120000});
  try {
    const missing=await exercise(run,provider,'does-not-exist','rv304-live-missing');
    facts.push(missing);await save('missing-model.json',missing);await save('request-facts.json',facts);
    assert.equal(missing.probe,'found');
    assert.equal(missing.recordStatus,'settled');
    assert.equal(missing.result.status,'failed');
    assert.ok([400,401,403,404,422].includes(missing.result.httpStatus));
    assert.equal(missing.settlements[0]?.status,'failed');
    assert.equal(missing.settlements[0]?.httpStatus,missing.result.httpStatus);

    const ok=await exercise(run,provider,'Qwen3.6-35B-A3B-FP8','rv304-live-ok');
    facts.push(ok);await save('normal-model.json',ok);await save('request-facts.json',facts);
    assert.equal(ok.probe,'found');
    assert.equal(ok.recordStatus,'settled');
    assert.equal(ok.result.status,'response');
    assert.equal(ok.settlements[0]?.status,'response');
    assert.equal(ok.result.httpStatus,200);
  } finally {
    await provider.close();
    await run.close();
  }
});
