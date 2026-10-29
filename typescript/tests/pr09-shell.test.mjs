import {test} from 'node:test';import assert from 'node:assert/strict';import {watch} from 'node:fs';import {setup} from './pr09-support.mjs';import Shell,{ShellExecutionError} from '../.build/extras/dist/shell/index.js';
test('PR09-T01 native Shell streams large output, drains cancellation, and replays the physically settled attempt',async()=>{
 const f=await setup();let watcher;
 try{
  await f.ctx.plugin(Shell,{chunkBytes:4096,killAfterMs:1000});const controller=new AbortController();const ready=Promise.withResolvers();watcher=watch(f.base,(_event,name)=>{if(String(name)==='shell-ready')ready.resolve()});
  const code=`const {once}=require('node:events');const fs=require('node:fs');process.on('SIGTERM',()=>setTimeout(()=>process.exit(23),120));(async()=>{for(let i=0;i<32;i++)if(!process.stdout.write(Buffer.alloc(65536,65)))await once(process.stdout,'drain');fs.writeFileSync('shell-ready','ready');setInterval(()=>{},1000)})()`;
  const request={argv:[process.execPath,'-e',code],cwd:f.base};const invocation={runId:f.runId,generation:'g',operationId:'shell-stream',signal:controller.signal};
  const running=f.ctx.shell.execute(request,invocation);const failure=running.then(()=>{throw Error('expected cancellation')},error=>error);
  await Promise.race([ready.promise,failure.then(error=>{throw error})]);const cancelledAt=Date.now();controller.abort(Error('consumer cancelled'));const error=await failure;assert.ok(error instanceof ShellExecutionError);const receipt=error.receipt;
  assert.equal(receipt.status,'cancelled');assert.equal(receipt.native.exitCode,23);assert.ok(receipt.native.closedAt>=cancelledAt+100);assert.equal(receipt.stdout.bytes,String(32*65536));let bytes=0;for await(const part of f.ctx.artifacts.read(receipt.stdout)){bytes+=part.length;assert.equal(part[0],65)}assert.equal(bytes,32*65536);
  const probe=await f.ctx.shell.probe('shell-stream');assert.equal(probe.status,'found');assert.equal(probe.record.status,'settled');assert.equal(probe.record.result.physicalRequestId,receipt.physicalRequestId);
  const replay=await f.ctx.shell.execute(request,{...invocation,signal:new AbortController().signal}).catch(error=>error);assert.ok(replay instanceof ShellExecutionError,String(replay?.stack??replay));assert.equal(replay.receipt.physicalRequestId,receipt.physicalRequestId);
  const limit=await f.ctx.shell.execute({argv:[process.execPath,'-e',"process.stdout.write('x'.repeat(1000000));setInterval(()=>{},1000)"],cwd:f.base,maxOutputBytes:4096},{...invocation,operationId:'shell-limit',signal:new AbortController().signal}).catch(error=>error);assert.equal(limit.receipt.status,'output-limit');assert.equal(limit.receipt.stdout.bytes,'4096');assert.equal((await f.ctx.shell.probe('shell-limit')).record.status,'settled');
 }finally{watcher?.close();await f.close()}
});
