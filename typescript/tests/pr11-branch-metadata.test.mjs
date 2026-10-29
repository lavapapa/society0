import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,stat,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {fixture,part,fullProfile} from './pr11-support.mjs';
test('large literal bootstrap profile retains complete plan through a bounded origin reference',async()=>{
 const f=await fixture();
 try{
  const journal=join(f.home,'branch-operations');
  await f.host.ctx.plugin((await part('host','run-branch')).default,{directory:journal});
  const directory=join(f.home,'large-profile'),selected=await fullProfile(f.home,'large-profile',directory,'g2','1');
  const bootstrap=selected.profile.patches[0].insert[0].config.find(entry=>entry.id==='bootstrap-scope');
  bootstrap.config[0].config.seed='literal-bootstrap-seed-'.repeat(60000);
  const plan=await f.host.ctx.runBranch.plan('source',f.root.id,{targetRunId:'large-profile',targetDirectory:directory,generation:'g2',controlSequence:'1',...selected,mount:{runEntryId:'run-scope',bootstrapEntryId:'bootstrap-scope'},migrations:[],seedPolicy:'preserve-seed-new-run-address'});
  assert.ok(Buffer.byteLength(JSON.stringify(plan))>1048576);
  const receipt=await f.host.ctx.runBranch.create(plan);
  const originPath=join(directory,'branch-origin.json');const origin=JSON.parse(await readFile(originPath,'utf8'));
  assert.ok((await stat(originPath)).size<4096);assert.equal(origin.operationId,plan.operationId);
  assert.equal(origin.source.pointId,f.root.id);assert.equal(origin.target.runId,'large-profile');
  const record=JSON.parse(await readFile(origin.plan.journalPath,'utf8'));
  assert.equal(origin.plan.operationId,record.plan.operationId);assert.deepEqual(record.plan,plan);
  assert.deepEqual(record.plan.sources,plan.sources);assert.equal(record.plan.target.profile.patches[0].insert[0].config.find(entry=>entry.id==='bootstrap-scope').config[0].config.seed,bootstrap.config[0].config.seed);
  assert.equal(receipt.point.lineage.sourcePointId,f.root.id);assert.equal((await f.host.ctx.runBranch.probe(plan.operationId)).live,true);
  const sourceDirectory=f.ctx.database.directory;assert.equal(sourceDirectory,join(f.home,'source'));assert.equal(await realpath(sourceDirectory),sourceDirectory);
  await f.handle.close();await rm(sourceDirectory,{recursive:true});
  assert.deepEqual(JSON.parse(await readFile(origin.plan.journalPath,'utf8')).plan,plan);
  const lease=f.host.ctx.runDirectory.acquire('large-profile','read');let observer,consumer;
  try{consumer=await lease.ctx.plugin({inject:['observation'],apply(ctx){observer=ctx.observation}});assert.equal((await observer.query({sql:'SELECT id FROM note'})).rows.length,5)}finally{await consumer?.dispose();await lease.release()}
  console.log(JSON.stringify({gate:'large-branch-origin',planBytes:Buffer.byteLength(JSON.stringify(plan)),originBytes:(await stat(originPath)).size,root:receipt.point.id}));
 }finally{await f.close()}
});

test('run-owned journal is rejected before native fork materialization',async()=>{
 const f=await fixture();
 try{
  await f.host.ctx.plugin((await part('host','run-branch')).default,{directory:join(f.home,'source','branch-operations')});
  const directory=join(f.home,'unsafe-journal'),selected=await fullProfile(f.home,'unsafe-journal',directory,'g2','1');
  const plan=await f.host.ctx.runBranch.plan('source',f.root.id,{targetRunId:'unsafe-journal',targetDirectory:directory,generation:'g2',controlSequence:'1',...selected,mount:{runEntryId:'run-scope',bootstrapEntryId:'bootstrap-scope'},migrations:[],seedPolicy:'preserve-seed-new-run-address'});
  const before=f.ctx.history.diagnostics();
  await assert.rejects(f.host.ctx.runBranch.create(plan),/BRANCH_JOURNAL_LIFETIME/);
  assert.equal((await f.ctx.history.probeFork({operationId:plan.operationId,targetDirectory:directory})).status,'not-prepared');
  await assert.rejects(stat(directory),{code:'ENOENT'});
  assert.equal(f.ctx.history.diagnostics().backupBytes,before.backupBytes);
  assert.equal((await f.host.ctx.runBranch.probe(plan.operationId)).status,'failed');
 }finally{await f.close()}
});
