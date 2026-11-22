import test from 'node:test';
import assert from 'node:assert/strict';
import {PiRuntime} from '../.build/core/dist/agent-runtime-pi/runtime.js';
import {OrderedInferenceSink} from '../.build/core/dist/agent-runtime-pi/sink.js';
import {ReplayProvider} from '../.build/core/dist/model-replay/provider.js';
import {fixture,assistant,extensions} from './pr08-support.mjs';

test('RV3-04-T02c replay error reports provider_error with empty modelAttempts',async()=>{
  const run=await fixture();let prepared,provider,runtime,sink;
  try {
    provider=new ReplayProvider({error:[{...assistant([], 'error'),errorMessage:'offline provider error'}]});
    const context=run.context('rv304-t02c');
    prepared=await provider.prepare({session:{actorId:'a',momentId:'m'},provider:'replay/v1',model:'error'},context);
    assert.equal(prepared.value.attemptSettlements,undefined);
    runtime=new PiRuntime();
    const abort=new AbortController();
    sink=new OrderedInferenceSink({maxPendingEvents:16,maxPendingBytes:65536},async()=>{},async()=>{},error=>abort.abort(error));
    const outcome=await runtime.run({threadId:'t',interactionId:'i',history:[],prompts:[{role:'user',content:'hi',timestamp:0}],tools:[],actionReceipts:{read:async()=>[]},selection:prepared.value,budget:{maxRequests:1,maxToolCalls:0},requiredActions:[]},{...context,signal:AbortSignal.any([context.signal,abort.signal])},sink,extensions());
    assert.equal(outcome.status,'incomplete');
    assert.equal(outcome.reason,'provider_error');
    assert.deepEqual(outcome.modelAttempts,[]);
    await runtime.drain('done');runtime=undefined;
  } finally {
    await prepared?.release().catch(()=>{});
    await provider?.close();
    await runtime?.drain('done').catch(()=>{});
    await run.close();
  }
});
