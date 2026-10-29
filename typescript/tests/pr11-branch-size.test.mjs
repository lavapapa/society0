import test from 'node:test';
import assert from 'node:assert/strict';
import {stat} from 'node:fs/promises';
import {join} from 'node:path';
import {fixture,part,fullProfile} from './pr11-support.mjs';
test('oversized origin generation is rejected before private materialization',async()=>{
 const f=await fixture();
 try{
  await f.host.ctx.plugin((await part('host','run-branch')).default,{directory:join(f.home,'branch-operations')});
  const directory=join(f.home,'origin-budget'),generation='g'.repeat(1048577),selected=await fullProfile(f.home,'origin-budget',directory,generation,'1');
  const plan=await f.host.ctx.runBranch.plan('source',f.root.id,{targetRunId:'origin-budget',targetDirectory:directory,generation,controlSequence:'1',...selected,mount:{runEntryId:'run-scope',bootstrapEntryId:'bootstrap-scope'},migrations:[],seedPolicy:'preserve-seed-new-run-address'});
  const before=f.ctx.history.diagnostics();
  await assert.rejects(f.host.ctx.runBranch.create(plan),/BRANCH_ORIGIN_BUDGET/);
  assert.equal((await f.ctx.history.probeFork({operationId:plan.operationId,targetDirectory:directory})).status,'not-prepared');
  await assert.rejects(stat(directory),{code:'ENOENT'});
  assert.equal(f.ctx.history.diagnostics().backupBytes,before.backupBytes);
  assert.equal((await f.host.ctx.runBranch.probe(plan.operationId)).status,'failed');
 }finally{await f.close()}
});
