import {appendFileSync,mkdirSync} from 'node:fs';import {join} from 'node:path';import test from 'node:test';import assert from 'node:assert/strict';import {writeFile} from 'node:fs/promises';
import {fixture,req} from './public-fault-support.mjs';import {installWorkerFault} from '../worker-fault.mjs';
const output=process.env.SOCIETY0_FAULT_OUTPUT;if(!output)throw Error('SOCIETY0_FAULT_OUTPUT is required (see worker-loss-kit/README.md)');mkdirSync(output,{recursive:true});const records=[];
for(const phase of ['before-begin','after-sql-dispatch','after-commit-reply'])test('public Cordis consumer / actual Worker '+phase,async()=>{
 const f=await fixture();const operationId='public-fault-'+phase;const reservation=await f.ctx.executionFence.reserve({id:operationId});const fault=installWorkerFault({phase,operationId,record:event=>appendFileSync(join(output,'events.jsonl'),JSON.stringify(event)+'\n')});let callbacks=0,error;
 try{
  await assert.rejects(f.ctx.database.transaction(reservation.lease,req(operationId),async tx=>{callbacks++;if(phase==='after-sql-dispatch')await tx.query({sql:'WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT sum(x) AS n FROM n'},{maxRows:1,maxBytes:128});else await tx.execute({sql:'INSERT INTO note VALUES (?,?)',parameters:[10n,'fault-proof']})}),value=>{error=value;return value instanceof Error});
  await reservation.release();const probe=await f.ctx.database.probe(operationId);const diagnostics=f.ctx.database.diagnostics();
  assert.ok(fault.events.some(x=>x.kind==='worker-exit'));assert.equal(diagnostics.halted,true);assert.equal(diagnostics.pendingTransactionBytes,0);assert.equal(f.ctx.executionFence.snapshot().acceptedCommits,0);
  assert.equal(probe.status,phase==='after-commit-reply'?'committed':'not-committed');assert.equal(callbacks,phase==='before-begin'?0:1);
  if(phase==='after-commit-reply'){assert.equal(error.code,'SQLITE_COMMIT_UNKNOWN');assert.equal(probe.receipt.operationId,operationId);assert.ok(fault.events.some(x=>x.kind==='commit-reply-observed'))}
  if(phase==='after-sql-dispatch')assert.ok(fault.events.some(x=>x.kind==='sql-dispatched'));
  records.push({phase,callbacks,error:{code:error.code,message:error.message},probe,events:fault.events,pendingTransactionBytes:diagnostics.pendingTransactionBytes});
 }finally{fault.restore();await reservation.release();await f.close()}
});
test.after(async()=>{await writeFile(join(output,'results.json'),JSON.stringify(records,null,2))});
