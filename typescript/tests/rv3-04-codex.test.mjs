import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore} from '@earendil-works/pi-ai';
import * as codex from '@earendil-works/pi-ai/api/openai-codex-responses';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {fixture} from './pr08-support.mjs';

const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url');
const token=()=>encode({alg:'none',typ:'JWT'})+'.'+encode({'https://api.openai.com/auth':{chatgpt_account_id:'fixture-account'}})+'.fixture';
const modelFor=port=>({id:'codex-offline',name:'codex-offline',api:'openai-codex-responses',provider:'codex-offline',baseUrl:'http://127.0.0.1:'+port,reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256});

async function exercise(run,port,name){
  const credentials=new InMemoryCredentialStore();
  await credentials.modify('codex-offline',async()=>({type:'oauth',access:token(),refresh:'fixture-refresh',expires:Date.now()+3600000}));
  const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  models.setProvider(createProvider({id:'codex-offline',models:[modelFor(port)],auth:{oauth:{name:'fixture oauth',isSubscription:true,login:async()=>{throw Error('LOGIN_UNUSED')},refresh:async()=>{throw Error('REFRESH_UNUSED')},toAuth:async credential=>({apiKey:credential.access})}},api:codex}));
  const provider=new PiModelProvider(models,run.ctx.attempts,{runId:'pr07',providerId:'codex-offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:2000});
  const context=run.context(name);
  let selected;
  try {
    selected=await provider.prepare({provider:'pi/v1',model:'codex-offline'},context);
    try {
      const stream=await selected.value.streamFn(selected.value.model,{messages:[{role:'user',content:'hi',timestamp:0}]},{});
      for await (const _ of stream) {}
    } catch {}
    await selected.value.drain();
    const settlements=[...(selected.value.attemptSettlements??[])];
    const probe=await run.ctx.attempts.probe({lineage:run.ctx.attempts.authority().lineage,operationId:context.operationId+'/model/1',attempt:0});
    return {settlements,probe};
  } finally {
    await selected?.release().catch(()=>{});
    await provider.close();
  }
}

async function withServer(handler){
  const server=createServer(handler);
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {port:server.address().port,close:()=>new Promise(resolve=>server.close(resolve))};
}
const complete=(status,body)=>(request,response)=>{request.resume();response.writeHead(status,{'content-type':'application/json'});response.end(body)};

test('RV3-04-T04 codex physical outcome follows recorded dispatch facts',async()=>{
  const run=await fixture();
  try {
    let server=await withServer(complete(400,JSON.stringify({error:{message:'bad request fixture'}})));
    let result=await exercise(run,server.port,'rv304-codex-400');
    await server.close();
    assert.equal(result.settlements[0]?.status,'failed');
    assert.equal(result.settlements[0]?.httpStatus,400);
    assert.equal(result.probe.record.status,'settled');
    assert.equal(result.probe.record.result.status,'failed');
    assert.equal(result.probe.record.result.httpStatus,400);

    server=await withServer(complete(429,JSON.stringify({error:{message:'usage limit fixture'}})));
    result=await exercise(run,server.port,'rv304-codex-429');
    await server.close();
    assert.equal(result.settlements[0]?.status,'unknown');
    assert.equal(result.probe.record.status,'unknown');
  } finally {
    await run.close();
  }
});
