import test from 'node:test';
import assert from 'node:assert/strict';
import {TransportObserver} from '../.build/core/dist/attempts/transport.js';

test('RV3-04-T01 abort before dispatch counts no physical call and settles not_dispatched', async () => {
  const observer=new TransportObserver('http://127.0.0.1:1/v1');
  const controller=new AbortController();
  controller.abort(Error('cancelled'));
  let dispatched=0;
  const wrapped=observer.fetch(async()=>{dispatched++;return new Response('{}')},1024,'FIXTURE_BYTES');
  await assert.rejects(wrapped('http://127.0.0.1:1/v1/chat/completions',{signal:controller.signal}));
  assert.equal(dispatched,0);
  assert.equal(observer.calls,0);
  assert.equal(observer.abortedBeforeDispatch,true);
  assert.equal(observer.classify(false),'not_dispatched');
  assert.equal(observer.cause(),null);
  const later=new TransportObserver('http://127.0.0.1:1/v1');
  assert.equal(later.classify(false),'unknown');
  assert.equal(later.cause(),'MODEL_TRANSPORT_UNOBSERVED');
});
