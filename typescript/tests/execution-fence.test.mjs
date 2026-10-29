import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ExecutionGate } from '../packages/core/src/execution-fence/state.ts';
function gate(runId='run-a') { return new ExecutionGate({runId,generation:'generation-1',readRevision:async()=> '4',maxAcceptedCommits:2}); }
test('a copied or other-run lease grants no write permission',async()=>{
 const a=gate(),b=gate('run-b');const execution=await a.reserve({id:'p1',observedRevision:'4'});
 assert.throws(()=>a.enter({...execution.lease}),/LEASE/);assert.throws(()=>b.enter(execution.lease),/LEASE/);
 const permit=a.enter(execution.lease);permit.release();await execution.release();
 assert.throws(()=>a.enter(execution.lease),/LEASE/);
});
test('reservation compares the durable revision before admission',async()=>{
 const a=gate();await assert.rejects(a.reserve({id:'old',observedRevision:'3'}),/STALE/);
 const accepted=await a.reserve({id:'new',observedRevision:'4'});await accepted.release();
});
test('admission is serialized across asynchronous revision reads',async()=>{
 let ready;const read=new Promise(r=>ready=r);const a=new ExecutionGate({runId:'r',generation:'g',readRevision:()=>read,maxAcceptedCommits:2});
 const first=a.reserve({id:'first'});await assert.rejects(a.reserve({id:'second'}),/BUSY/);ready('0');const e=await first;await e.release();
});
test('cancel rejects late writes and retains active permits until release',async()=>{
 const a=gate();const e=await a.reserve({id:'p'});const permit=a.enter(e.lease);e.cancel('stopped');
 assert.throws(()=>a.enter(e.lease),/LEASE/);let done=false;const draining=e.release().then(()=>done=true);
 await new Promise(r=>setImmediate(r));assert.equal(done,false);assert.equal(a.snapshot().acceptedCommits,1);
 permit.release();permit.release();await draining;assert.equal(a.snapshot().acceptedCommits,0);
});
test('seal requires settled execution and rejects new writes until explicit resume',async()=>{
 const a=gate();const e=await a.reserve({id:'p'});await assert.rejects(a.seal(),/BUSY/);await e.release();
 const publication=await a.seal();assert.equal(publication.sealedRevision,'4');await assert.rejects(a.reserve({id:'later'}),/BUSY/);
 assert.throws(()=>a.resume({...publication}),/PUBLICATION/);a.resume(publication);assert.throws(()=>a.resume(publication),/PUBLICATION/);
 const next=await a.reserve({id:'next'});await next.release();
});
test('commit admission is bounded and halt cannot be undone by a late seal',async()=>{
 const a=gate();const e=await a.reserve({id:'p'});const p=a.enter(e.lease),q=a.enter(e.lease);assert.throws(()=>a.enter(e.lease),/CAPACITY/);p.release();q.release();await e.release();
 a.halt('unknown commit');await assert.rejects(a.seal(),/BUSY/);await assert.rejects(a.reserve({id:'next'}),/BUSY/);assert.equal(a.snapshot().state,'halted');
});
test('halt during admission never issues a write lease',async()=>{
 let ready;const a=new ExecutionGate({runId:'r',generation:'g',readRevision:()=>new Promise(r=>ready=r),maxAcceptedCommits:1});
 const pending=a.reserve({id:'p'});a.halt('close');ready('0');await assert.rejects(pending,/HALTED/);assert.equal(a.snapshot().state,'halted');
});

test('late sealing failure preserves the original halt cause',async()=>{
 let ready;const a=new ExecutionGate({runId:'r',generation:'g',readRevision:()=>new Promise(r=>ready=r),maxAcceptedCommits:1});
 const pending=a.seal();a.halt('worker commit result unknown');ready('0');
 await assert.rejects(pending,/HALTED/);assert.equal(a.snapshot().reason,'worker commit result unknown');
});
test('owner close remains pending until accepted physical work settles',async()=>{
 const a=gate();const execution=await a.reserve({id:'p'});const permit=a.enter(execution.lease);
 let done=false;const close=a.close('owner retired').then(()=>done=true);await new Promise(r=>setImmediate(r));
 assert.equal(done,false);assert.throws(()=>permit.assertActive(),/owner retired/);permit.release();await close;
 assert.equal(a.snapshot().state,'halted');assert.equal(a.snapshot().acceptedCommits,0);
});

test('invalid identity or capacity is rejected at construction',()=>{
 const options={runId:'r',generation:'g',readRevision:async()=> '0',maxAcceptedCommits:1};
 for(const maxAcceptedCommits of [0,-1,0.5,Infinity,NaN,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>new ExecutionGate({...options,maxAcceptedCommits}),/CAPACITY/);
 assert.throws(()=>new ExecutionGate({...options,runId:''}),/IDENTITY/);assert.throws(()=>new ExecutionGate({...options,generation:''}),/IDENTITY/);
});
test('invalid revision and failed seal do not create authority',async()=>{
 for(const revision of ['01','-1',1,undefined]){const a=new ExecutionGate({runId:'r',generation:'g',readRevision:async()=>revision,maxAcceptedCommits:1});await assert.rejects(a.reserve({id:'p'}),/REVISION/);assert.equal(a.snapshot().state,'idle')}
 const a=new ExecutionGate({runId:'r',generation:'g',readRevision:async()=>{throw Error('disk read failed')},maxAcceptedCommits:1});
 await assert.rejects(a.seal(),/disk read failed/);assert.equal(a.snapshot().state,'halted');await a.close();
});
test('normal release closes admission but waits for every existing permit',async()=>{
 const a=gate();const e=await a.reserve({id:'p'}),p=a.enter(e.lease),q=a.enter(e.lease);
 let done=false;const release=e.release();assert.equal(release,e.release());void release.then(()=>done=true);
 assert.throws(()=>a.enter(e.lease),/LEASE/);p.assertActive();q.assertActive();p.release();await new Promise(r=>setImmediate(r));assert.equal(done,false);
 assert.throws(()=>p.assertActive(),/PERMIT/);q.release();await release;assert.equal(a.snapshot().state,'idle');await a.close();
});

test('non-Error revision failures retain their diagnostic',async()=>{
 const a=new ExecutionGate({runId:'r',generation:'g',readRevision:async()=>{throw 'revision unavailable';},maxAcceptedCommits:1});
 await assert.rejects(a.seal(),error=>error==='revision unavailable');
 assert.equal(a.snapshot().reason,'revision unavailable');
});
test('owner close does not cancel an already settled reservation',async()=>{
 const a=gate();const e=await a.reserve({id:'finished'});await e.release();await a.close();
 assert.equal(e.signal.aborted,false);assert.equal(a.snapshot().acceptedCommits,0);
});
