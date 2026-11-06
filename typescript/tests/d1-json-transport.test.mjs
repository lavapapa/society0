import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createModels,createProvider,InMemoryCredentialStore,envApiKeyAuth} from '@earendil-works/pi-ai';
import * as completions from '@earendil-works/pi-ai/api/openai-completions';
import {PiModelProvider} from '../.build/core/dist/model-pi/provider.js';
import {resolveTransport} from '../.build/core/dist/model-pi/json-transport.js';
import {fixture} from './pr08-support.mjs';

const model={id:'offline',name:'offline',api:'openai-completions',provider:'offline',baseUrl:'',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:8192,maxTokens:256};
async function gateway(run,{transport='json',jsonTimeoutMs=180000,handler}){
  const received=[],sockets=new Set();
  const server=createServer(async(request,response)=>{const chunks=[];for await(const chunk of request)chunks.push(chunk);const body=Buffer.concat(chunks).toString();received.push(body);await handler(body,response)});
  server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket))});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const credentials=new InMemoryCredentialStore();await credentials.modify('offline',async()=>({type:'api_key',key:'offline-fixture-key'}));
  const selectedModel={...model,baseUrl:'http://127.0.0.1:'+server.address().port+'/v1'};
  const models=createModels({credentials,authContext:{env:async()=>undefined,fileExists:async()=>false}});
  models.setProvider(createProvider({id:'offline',models:[selectedModel],auth:{apiKey:envApiKeyAuth('explicit',[])},api:completions}));
  const provider=new PiModelProvider(models,run.ctx.attempts,{runId:'pr07',providerId:'offline',maxEventBytes:65536,maxRawBytes:1048576,maxRequestBytes:1048576,options:{maxTokens:256,maxRetries:0,transport:'sse'},timeoutMs:5000,jsonTimeoutMs,transport});
  return {server,provider,received,close:async()=>{await provider.close();server.closeAllConnections();for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve))}};
}
async function call(run,provider,context,options={}){
  const selected=await provider.prepare({provider:'pi/v1',model:'offline'},context);
  const events=[];let error,message;
  try{const stream=await selected.value.streamFn(selected.value.model,{messages:[{role:'user',content:'fixture',timestamp:0}]},options);try{for await(const event of stream)events.push(event)}catch(cause){error=cause}try{message=await stream.result()}catch(cause){error??=cause}}catch(cause){error=cause}
  try{await selected.value.drain()}catch(cause){error??=cause}
  await selected.release();
  return {events,error,message};
}
async function attempt(run,context){return run.ctx.attempts.probe({lineage:run.ctx.attempts.authority().lineage,operationId:context.operationId+'/model/1',attempt:0})}
async function artifactText(run,ref){let text='';for await(const part of run.ctx.attempts.readArtifact(ref))text+=Buffer.from(part).toString();return text}

test('D1 default transport is json and sse is per-model opt-in',()=>{
  assert.equal(resolveTransport(undefined,'m'),'json');
  assert.equal(resolveTransport({},'m'),'json');
  assert.equal(resolveTransport({m:'sse'},'m'),'sse');
  assert.equal(resolveTransport({other:'sse'},'m'),'json');
});

test('D1 json transport sends stream:false and reassembles tool calls with usage and evidence',async t=>{
  const run=await fixture();t.after(()=>run.close());
  const calls=[{id:'call_a',type:'function',function:{name:'read',arguments:'{"path":"/workspace/a.txt"}'}},{id:'call_b',type:'function',function:{name:'grep',arguments:'{"pattern":"needle"}'}}];
  const payload={id:'json-tools',object:'chat.completion',created:1700000000,model:'offline',choices:[{index:0,message:{role:'assistant',content:null,tool_calls:calls},finish_reason:'tool_calls'}],usage:{prompt_tokens:11,completion_tokens:7,total_tokens:18}};
  const gate=await gateway(run,{handler:async(_body,response)=>{response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify(payload))}});
  t.after(()=>gate.close());
  const context=run.context('json-tools');
  const {message,error}=await call(run,gate.provider,context);
  assert.equal(error,undefined);
  const body=JSON.parse(gate.received[0]);
  assert.equal(body.stream,false);
  assert.equal('stream_options' in body,false);
  assert.equal(message.stopReason,'toolUse');
  const tools=message.content.filter(part=>part.type==='toolCall');
  assert.equal(tools.length,2);
  assert.deepEqual(tools.map(part=>[part.id,part.name]),[['call_a','read'],['call_b','grep']]);
  assert.equal(JSON.stringify(tools.map(part=>part.arguments)),JSON.stringify([{path:'/workspace/a.txt'},{pattern:'needle'}]));
  assert.equal(message.usage.input,11);
  assert.equal(message.usage.output,7);
  assert.equal(message.usage.totalTokens,18);
  const found=await attempt(run,context);
  assert.equal(found.record.status,'settled');
  let wire=false,raw=false,synthetic=false;
  for(const ref of found.record.artifacts){const text=await artifactText(run,ref);if(text.includes('"stream":false')&&text.includes('"messages"'))wire=true;if(text.includes('"tool_calls"')&&!text.includes('synthetic'))raw=true;if(text.includes('synthetic')&&text.includes('tool_calls'))synthetic=true}
  assert.equal(wire,true,'wire body archived');
  assert.equal(raw,true,'raw json archived');
  assert.equal(synthetic,true,'synthetic raw events marked');
});

test('D1 json text response records usage',async t=>{
  const run=await fixture();t.after(()=>run.close());
  const gate=await gateway(run,{handler:async(_body,response)=>{response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify({id:'json-text',object:'chat.completion',created:1,model:'offline',choices:[{index:0,message:{role:'assistant',content:'hello'},finish_reason:'stop'}],usage:{prompt_tokens:3,completion_tokens:2,total_tokens:5}}))}});
  t.after(()=>gate.close());
  const {message,error}=await call(run,gate.provider,run.context('json-text'));
  assert.equal(error,undefined);
  assert.equal(message.stopReason,'stop');
  assert.equal(message.content.find(part=>part.type==='text')?.text,'hello');
  assert.equal(message.usage.totalTokens,5);
});

async function failureStatus(run,transport,handler,label){
  const gate=await gateway(run,{transport,handler});
  try{const context=run.context(label);const {message,error}=await call(run,gate.provider,context);const found=await attempt(run,context);return {message,error,status:found.record.result?.status??found.record.status}}finally{await gate.close()}
}
test('D1 json failures settle like the same sse failures',async t=>{
  const run=await fixture();t.after(()=>run.close());
  const http500=async(_body,response)=>{response.writeHead(500,{'content-type':'application/json'});response.end('{"error":{"message":"boom"}}')};
  const badJson=async(_body,response)=>{response.writeHead(200,{'content-type':'application/json'});response.end('not-json')};
  const emptyChoices=async(_body,response)=>{response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify({id:'empty',object:'chat.completion',choices:[]}))};
  const sseBad=async(_body,response)=>{response.writeHead(200,{'content-type':'text/event-stream'});response.end('data: not-json\n\n')};
  const sseEmpty=async(_body,response)=>{response.writeHead(200,{'content-type':'text/event-stream'});response.end('data: '+JSON.stringify({choices:[]})+'\n\ndata: [DONE]\n\n')};
  for(const [name,jsonHandler,sseHandler,equal] of [['http500',http500,http500,true],['bad-json',badJson,sseBad,false],['empty-choices',emptyChoices,sseEmpty,true]]){
    await t.test(name,async()=>{
      const json=await failureStatus(run,'json',jsonHandler,'json-'+name),sse=await failureStatus(run,'sse',sseHandler,'sse-'+name);
      assert.ok(json.error||json.message?.stopReason==='error',name+' json fails');
      if(equal)assert.equal(json.status,sse.status,'same settlement status for '+name);
      else assert.equal(json.status,'response','fully received json body settles as response');
    });
  }
});

test('D1 abort while waiting for the json response terminates the request',async t=>{
  const run=await fixture();t.after(()=>run.close());
  const received=Promise.withResolvers(),closed=Promise.withResolvers();
  const gate=await gateway(run,{handler:async(_body,response)=>{received.resolve();response.on('close',()=>closed.resolve())}});
  t.after(()=>gate.close());
  const controller=new AbortController();
  const pending=call(run,gate.provider,run.context('json-abort'),{signal:controller.signal});
  await received.promise;
  controller.abort(Error('client abort'));
  const {error,message}=await pending;
  assert.ok(error||message?.stopReason==='aborted');
  await Promise.race([closed.promise,new Promise((_,reject)=>setTimeout(()=>reject(Error('response not closed')),5000))]);
});

test('D1 jsonTimeoutMs bounds the whole request',async t=>{
  const run=await fixture();t.after(()=>run.close());
  const gate=await gateway(run,{jsonTimeoutMs:100,handler:async()=>{}});
  t.after(()=>gate.close());
  const started=Date.now();
  const {error,message}=await call(run,gate.provider,run.context('json-timeout'));
  assert.ok(error||message?.stopReason==='error');
  assert.ok(Date.now()-started<30000,'timeout terminates well before the 180s json default');
});
