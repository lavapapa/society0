import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {EmbeddingQueue} from '../.build/core/dist/embedding/broker.js';
import {HttpEmbeddingProvider} from '../.build/core/dist/embedding-http/provider.js';
import {fixture} from './pr08-support.mjs';

const limits={maxTextBytes:1024,maxPendingBytes:1<<20,maxBatchItems:1,maxBatchBytes:1024,maxWaitMs:1,maxCacheItems:0,maxCacheBytes:0,scheduling:'fifo'};
const identity={provider:'p',configId:'c',model:'m',dimensions:2};
function fakeLedger(options={}) {
  const calls={claim:0,settle:0,markUnknown:0};let sequence=0;
  const ledger={calls,authority:()=>({lineage:'l'}),evidenceChunkBytes:1024,lastUnknown:undefined,lastUnknownRefs:undefined,
    async stageEvidence(source,mediaType){for await(const _ of source){}sequence++;return {id:'artifact-'+sequence,bytes:'1',mediaType}},
    async claim(){calls.claim++;return {status:'send-permitted',identity,physicalRequestId:'physical'}},
    async settle(){calls.settle++;if(options.settle)throw options.settle},
    async markUnknown(_identity,reason,refs){calls.markUnknown++;this.lastUnknown=reason;this.lastUnknownRefs=refs;if(options.markUnknown)throw options.markUnknown}}
  return ledger;
}
const context=()=>({runId:'r',operationId:'op',signal:new AbortController().signal});
async function start(handler){let counter=0;const server=createServer((request,response)=>{counter++;request.resume();handler(response)});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return {server,port:server.address().port,calls:()=>counter,close:async()=>{server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve))}}}

test('NEW-3-T02 truncated response body is unknown and poisons the queue', async () => {
  const ledger=fakeLedger();
  const endpoint=await start(response=>{response.writeHead(200,{'content-type':'application/json'});response.write('{"data":[{"index":0,');setTimeout(()=>response.destroy(),100)});
  const provider=new HttpEmbeddingProvider({identity,url:'http://127.0.0.1:'+endpoint.port+'/embed',credentialProvider:'',timeoutMs:1000,maxResponseBytes:65536,maxRequestBytes:4096},undefined,ledger);
  const queue=new EmbeddingQueue('r',provider,limits,ledger);
  try {
    let error;
    await assert.rejects(queue.embed({identity,texts:['x']},context()),cause=>{error=cause;return true});
    assert.equal(error.physical,'unknown');
    assert.equal(ledger.calls.markUnknown,1);
    assert.equal(ledger.calls.settle,0);
    assert.ok((ledger.lastUnknownRefs??[]).length>=1);
    await assert.rejects(queue.embed({identity,texts:['y']},context()));
    assert.equal(endpoint.calls(),1);
  } finally {
    await queue.close();
    await endpoint.close();
  }
});

test('NEW-3-T01 settlement failure keeps the original response evidence in unknown artifacts', async () => {
  const ledger=fakeLedger({settle:Error('LEDGER_DOWN')});
  const endpoint=await start(response=>{response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify({data:[{index:0,embedding:[1,0]}],usage:{tokens:1}}))});
  const provider=new HttpEmbeddingProvider({identity,url:'http://127.0.0.1:'+endpoint.port+'/embed',credentialProvider:'',timeoutMs:1000,maxResponseBytes:65536,maxRequestBytes:4096},undefined,ledger);
  const queue=new EmbeddingQueue('r',provider,limits,ledger);
  try {
    let error;
    await assert.rejects(queue.embed({identity,texts:['x']},context()),cause=>{error=cause;return true});
    assert.equal(error.message,'LEDGER_DOWN');
    assert.equal(ledger.calls.settle,1);
    assert.equal(ledger.calls.markUnknown,1);
    const ids=(ledger.lastUnknownRefs??[]).map(ref=>ref.id);
    assert.ok(ids.includes('artifact-2'),JSON.stringify(ids));
    assert.ok(ids.includes('artifact-1'),JSON.stringify(ids));
  } finally {
    await queue.close();
    await endpoint.close();
  }
});

test('NEW-3-T02 invalid response keeps provider original artifacts and queue continues',async()=>{
  const run=await fixture();let requests=0;
  const server=createServer((request,response)=>{requests++;request.resume();response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify(requests===1?{data:[{index:0,embedding:[1]}],usage:{}}:{data:[{index:0,embedding:[1,0]}],usage:{tokens:1}}))});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const provider=new HttpEmbeddingProvider({identity,url:'http://127.0.0.1:'+server.address().port+'/embed',credentialProvider:'',timeoutMs:1000,maxResponseBytes:65536,maxRequestBytes:4096},undefined,run.ctx.attempts);
  const queue=new EmbeddingQueue('r',provider,limits,run.ctx.attempts);
  const ctx=operationId=>({runId:'r',operationId,signal:new AbortController().signal});
  try {
    await assert.rejects(queue.embed({identity,texts:['x']},ctx('op-invalid')));
    const probe=await run.ctx.attempts.probe({lineage:run.ctx.attempts.authority().lineage,operationId:'run/r/embedding/p/c/op-invalid/0',attempt:0});
    assert.equal(probe.record.status,'settled');
    assert.equal(probe.record.result.status,'invalid_response');
    assert.equal(probe.record.artifacts.length,2,JSON.stringify(probe.record.artifacts));
    assert.notEqual(probe.record.artifacts[0].id,probe.record.artifacts[1].id);
    const output=await queue.embed({identity,texts:['y']},ctx('op-valid'));
    assert.deepEqual(output[0].vector,[1,0]);
  } finally {
    await queue.close();
    await new Promise(resolve=>server.close(resolve));
    await run.close();
  }
});
