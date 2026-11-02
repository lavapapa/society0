import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
import {waitForPhase} from './history-phase-support.mjs';

test('RV3-02-T06 phase wait accepts readiness while operation remains held',async()=>{
 const phase=Promise.withResolvers(),operation=Promise.withResolvers();
 phase.resolve();await waitForPhase(phase.promise,operation.promise,'compact hook');operation.resolve();
});
test('RV3-02-T06 phase wait propagates the actual early operation error',async()=>{
 const failure=Error('native source unavailable'),phase=Promise.withResolvers();
 const operation=(async()=>{throw failure})();void operation.catch(()=>{});
 await assert.rejects(waitForPhase(phase.promise,operation,'compact hook'),error=>error===failure);
});
test('RV3-02-T06 phase wait rejects successful completion without readiness',async()=>{
 await assert.rejects(waitForPhase(new Promise(()=>{}),Promise.resolve(),'compact hook'),/operation completed before phase/);
});
test('RV3-02-T06 phase wait admits real preparation beyond the former hook guard',async()=>{
 const phase=Promise.withResolvers(),gate=Promise.withResolvers();let reached=false;
 const operation=(async()=>{await delay(11000);reached=true;phase.resolve();await gate.promise})();
 try{await waitForPhase(phase.promise,operation,'compact hook');assert.equal(reached,true)}
 finally{gate.resolve();await operation}
});
