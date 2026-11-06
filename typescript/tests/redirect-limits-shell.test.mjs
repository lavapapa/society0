import {test} from 'node:test';import assert from 'node:assert/strict';
import {setup as coreSetup,scope} from './pr07-support.mjs';import {setup as extrasSetup} from './pr09-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';import Shell,{ShellTimeoutError} from '../.build/extras/dist/shell/index.js';

test('redirect-limits/extras shell timeoutMs physically terminates an over-budget command',async()=>{
 const f=await extrasSetup();
 try{
  assert.equal(Shell.Config({}).timeoutMs,30000);
  await f.ctx.plugin(Shell,{timeoutMs:500,killAfterMs:100});
  const invocation={runId:f.runId,generation:'g',operationId:'redirect-shell-timeout',signal:new AbortController().signal};
  const started=Date.now();let error;
  try{await f.ctx.shell.execute({argv:[process.execPath,'-e','setInterval(()=>{},1000)'],cwd:f.base},invocation)}catch(cause){error=cause}
  const elapsed=Date.now()-started;
  assert.ok(error, 'command should fail on timeout');
  assert.ok(error instanceof ShellTimeoutError,String(error?.stack??error));
  assert.equal(error.code,'SHELL_TIMEOUT');
  assert.equal(error.receipt.status,'cancelled');
  assert.equal(error.receipt.native.cancelRequested,true);
  assert.ok(elapsed>=500&&elapsed<3000, 'elapsed '+elapsed);
  const probe=await f.ctx.shell.probe('redirect-shell-timeout');
  assert.equal(probe.status,'found');
  assert.equal(probe.record.status,'settled');
  assert.equal(probe.record.result.status,'cancelled');
 }finally{await f.close()}
});

test('redirect-limits/exploration-shell maxCommandCount rejects an interpreter run over budget',async()=>{
 const env=await coreSetup();let session;
 try{
  assert.equal(ExplorationShell.Config({}).maxCommandCount,10000);
  await env.host.ctx.plugin(ExplorationShell,{timeoutMs:120000,maxCommandCount:2});
  let ctx;await env.host.ctx.plugin({inject:['explorationShell','exploration','tasks','database'],apply(c){ctx=c}});
  const base=env.context('redirect-command-count');
  const context={...base,actionRegistrationId:'shell',transaction:(request,body)=>ctx.database.transaction(base.writeLease,request,body)};
  session=await ctx.exploration.acquire(scope,context);
  const over=await ctx.explorationShell.execute('echo a; echo b; echo c',session.value,context);
  assert.equal(over.exitCode,126);
  assert.match(over.stderr,/too many commands/);
  assert.equal(ctx.tasks.snapshot().activeCount,0);
  assert.equal(ctx.tasks.snapshot().pendingCount,0);
  assert.equal(ctx.database.diagnostics().inflightOperations,0);
 }finally{try{await session?.release()}catch{}await env.close()}
});
