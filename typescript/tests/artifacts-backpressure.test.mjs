import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {LocalArtifacts} from '../.build/core/dist/artifacts-local/index.js';

// maxActive 是在途操作上限：超出时排队等待槽位，而不是让调用方随机失败（ARTIFACT_BUSY 曾使并发激活中途被中止）。
async function* slow(gate,size=1024){await gate;yield Buffer.alloc(size,1)}

test('artifacts-local：超过 maxActive 的 stage 排队等待，在途数不超过上限且全部成功',async()=>{
  const home=await mkdtemp(join(tmpdir(),'artifacts-backpressure-'));const store=await LocalArtifacts.open({directory:join(home,'a'),maxActive:2});
  try {
    const gate=Promise.withResolvers();let peak=0;
    const works=Array.from({length:6},()=>store.stage(slow(gate.promise),'application/octet-stream'));
    for(let i=0;i<5;i++){await new Promise(resolve=>setImmediate(resolve));peak=Math.max(peak,store.diagnostics().active)}
    assert.equal(peak,2);
    gate.resolve();
    const refs=await Promise.all(works);
    assert.equal(new Set(refs.map(ref=>ref.id)).size,6);
    assert.equal(store.diagnostics().active,0);
  } finally {await store.close();await rm(home,{recursive:true,force:true})}
});

test('artifacts-local：排队中取消即以取消原因拒绝且不占槽位；关闭时排队者以 ARTIFACT_CLOSED 拒绝',async()=>{
  const home=await mkdtemp(join(tmpdir(),'artifacts-backpressure-'));const store=await LocalArtifacts.open({directory:join(home,'a'),maxActive:1});
  try {
    const gate=Promise.withResolvers(),controller=new AbortController();
    const holder=store.stage(slow(gate.promise),'application/octet-stream');
    const cancelled=store.stage(slow(Promise.resolve()),'application/octet-stream',{signal:controller.signal});
    controller.abort(Object.assign(Error('stop'),{code:'STOP'}));
    await assert.rejects(cancelled,error=>error.code==='STOP');
    const queued=store.stage(slow(Promise.resolve()),'application/octet-stream');
    gate.resolve();await holder;
    await queued;
    const blocker=Promise.withResolvers();
    const busy=store.stage(slow(blocker.promise),'application/octet-stream'),late=store.stage(slow(Promise.resolve()),'application/octet-stream');
    const closing=store.close();
    await assert.rejects(late,error=>error.code==='ARTIFACT_CLOSED');
    blocker.resolve();await busy;await closing;
  } finally {await store.close();await rm(home,{recursive:true,force:true})}
});
