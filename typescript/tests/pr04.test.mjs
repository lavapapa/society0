import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Database} from '../.build/core/dist/sqlite/database.js';
import {ExecutionGate} from '../.build/core/dist/execution-fence/state.js';
const request=(operationId)=>({operationId,maxDurationMs:15000,maxStatements:20,maxInputBytes:65536,maxChangedRows:100,maxCapturedBytes:1048576});
async function fixture(runId='one') {
 const directory=await mkdtemp(join(tmpdir(),'pr04-'));
 let db;const gate=new ExecutionGate({runId,generation:'g1',maxAcceptedCommits:20,readRevision:()=>db.readRevision()});
 db=await Database.open({directory,runId,maxTransactionMs:20000},gate);
 try {await db.applyMigrations([{owner:'notes',from:0,to:1,after:[],source:{package:'notes',version:'1'},statements:[{sql:'CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT NOT NULL)'},{sql:'CREATE TABLE actions(id INTEGER PRIMARY KEY, note INTEGER NOT NULL REFERENCES notes(id), label TEXT NOT NULL)'}]}]);
 }catch(error){console.error(error);await db.close();throw error}
 return {db,gate,directory};
}
test('PR04-T01 real worker two-domain SQL commits or rolls back together',async()=>{
 const {db,gate}=await fixture();const reservation=await gate.reserve({id:'proposal'});
 try {
  let saved;
  await assert.rejects(db.transaction(reservation.lease,request('failed'),async tx=>{saved=tx;await tx.execute({sql:'INSERT INTO notes VALUES (?,?)',parameters:[1n,'one']});await tx.execute({sql:'INSERT INTO actions VALUES (1,99,?)',parameters:['bad']});}),/FOREIGN KEY/);
  let snap=await db.snapshot({maxAgeMs:1000});assert.equal((await snap.query({sql:'SELECT * FROM notes'},{maxRows:10,maxBytes:1024})).length,0);await snap.close();
  const committed=await db.transaction(reservation.lease,request('ok'),async tx=>{await tx.execute({sql:'INSERT INTO notes VALUES (?,?)',parameters:[1n,'one']});await tx.execute({sql:'INSERT INTO actions VALUES (1,1,?)',parameters:['ok']});return 'both';});
  assert.equal(committed.value,'both');assert.equal(committed.receipt.revision,'1');
  let staleRan=false;await assert.rejects(db.transaction(reservation.lease,{...request('stale-result'),expectedRevision:'0'},async()=>{staleRan=true}),/STALE_REVISION/);assert.equal(staleRan,false);
  assert.equal((await db.probe('ok')).status,'committed');
  await assert.rejects(saved.execute({sql:'DELETE FROM notes'}),/EXPIRED/);
  snap=await db.snapshot({maxAgeMs:1000});assert.equal((await snap.query({sql:'SELECT notes.body,actions.label FROM notes JOIN actions ON actions.note=notes.id'},{maxRows:10,maxBytes:1024}))[0].body,'one');await snap.close();
 }finally{await reservation.release();await db.close()}
});

test('PR04-T03/T06 runs isolate identical SQL and reject foreign or forged leases',async()=>{
 const a=await fixture('a'),b=await fixture('b');const reservation=await a.gate.reserve({id:'a'});
 try{
  await assert.rejects(b.db.transaction(reservation.lease,request('cross'),async()=>{}),/FENCE_LEASE/);
  await assert.rejects(a.db.transaction({...reservation.lease},request('forged'),async()=>{}),/FENCE_LEASE/);
  await a.db.transaction(reservation.lease,request('a-write'),tx=>tx.execute({sql:'INSERT INTO notes VALUES (1,?)',parameters:['only-a']}));
  const sa=await a.db.snapshot({maxAgeMs:30000}),sb=await b.db.snapshot({maxAgeMs:30000});
  assert.equal((await sa.query({sql:'SELECT * FROM notes'},{maxRows:5,maxBytes:1024})).length,1);
  assert.equal((await sb.query({sql:'SELECT * FROM notes'},{maxRows:5,maxBytes:1024})).length,0);
  await sa.close();await sb.close();
 }finally{await reservation.release();await a.db.close();await b.db.close()}
});

test('PR04-T05/T08 query bounds, batch rollback, BLOB ranges, and pinned WAL view',async()=>{
 const {db,gate}=await fixture('bounds');await db.applyMigrations([{owner:'blob',from:0,to:1,after:['notes'],source:{version:'1'},statements:[{sql:'CREATE TABLE bodies(id INTEGER PRIMARY KEY, value BLOB NOT NULL)'}]}]);
 const r=await gate.reserve({id:'bounds'});
 try{
  const before=db.diagnostics().requests;
  await db.transaction(r.lease,request('batch'),tx=>tx.executeMany('INSERT INTO notes VALUES (?,?)',[[1,'first'],[2,'second']],{maxRows:2,maxInputBytes:4096}));
  assert.equal(db.diagnostics().requests-before,3,'BEGIN + one batch RPC + COMMIT');
  const snapshot=await db.snapshot({maxAgeMs:30000});
  await db.transaction(r.lease,request('update'),async tx=>{await tx.execute({sql:'UPDATE notes SET body=? WHERE id=1',parameters:['new']});await tx.execute({sql:'INSERT INTO bodies VALUES (1,zeroblob(262144))'});});
  assert.equal((await snapshot.query({sql:'SELECT body FROM notes WHERE id=1'},{maxRows:1,maxBytes:100}))[0].body,'first');
  const current=await db.snapshot({maxAgeMs:30000});
  assert.equal((await current.readBlobRange({table:'bodies',column:'value',rowid:1n,offset:250000,length:32,maxBytes:32})).length,32);
  await assert.rejects(current.query({sql:'SELECT * FROM notes'},{maxRows:1,maxBytes:100}),/LIMIT|maxRows|maxBytes/);
  await assert.rejects(current.query({sql:'SELECT * FROM notes'},{maxRows:5,maxBytes:1}),/LIMIT|maxRows|maxBytes/);
  await current.close();await assert.rejects(current.readBlobRange({table:'bodies',column:'value',rowid:1n,offset:0,length:1,maxBytes:1}),/EXPIRED/);
  await snapshot.close();
  await assert.rejects(db.transaction(r.lease,request('batch-bad'),tx=>tx.executeMany('INSERT INTO notes VALUES (?,?)',[[3,'third'],[1,'duplicate']],{maxRows:2,maxInputBytes:4096})),/UNIQUE/);
  const final=await db.snapshot({maxAgeMs:30000});assert.equal((await final.query({sql:'SELECT * FROM notes'},{maxRows:10,maxBytes:1024})).length,2);await final.close();
  const native=await db.rpc.call('state');assert.equal(native.schemaEnumerations,1);assert.ok(native.sessionAttachCount>=21);assert.equal(native.tableValidationCount,7);
  const cancel=new AbortController();const currentRead=db.rpc.call('revision');const queued=db.rpc.call('revision',{},cancel.signal);const rejected=assert.rejects(queued,/queued cancel/);cancel.abort(Error('queued cancel'));assert.equal(await currentRead,'2');await rejected;
  await assert.rejects(db.rpc.call('query',{statement:{sql:'SELECT ?',parameters:[new Uint8Array(33*1024*1024)]}}),/QUEUE_LIMIT/);
  await assert.rejects(db.transaction(r.lease,{...request('input-limit'),maxInputBytes:100},tx=>tx.execute({sql:'INSERT INTO notes VALUES (4,?)',parameters:['x'.repeat(200)]})),/BUDGET|LIMIT/);
  await assert.rejects(db.transaction(r.lease,{...request('row-limit'),maxChangedRows:1},tx=>tx.executeMany('INSERT INTO notes VALUES (?,?)',[[4,'four'],[5,'five']],{maxRows:2,maxInputBytes:4096})),/CHANGED_ROWS/);
  await assert.rejects(db.transaction(r.lease,{...request('capture-limit'),maxCapturedBytes:1024},tx=>tx.execute({sql:'INSERT INTO bodies VALUES (2,zeroblob(65536))'})),/capture|CAPTURE|limit|LIMIT|memory/i);
  await assert.rejects(db.transaction(r.lease,request('return-limit'),async tx=>{await tx.execute({sql:'INSERT INTO notes VALUES (6,?)',parameters:['six']});return new Uint8Array(1048577)}),/RETURN_BUDGET/);
  await assert.rejects(db.transaction(r.lease,request('participant-commit'),tx=>{assert.equal(tx.commit,undefined);return tx.execute({sql:'COMMIT'})}),/AUTH|authorized|control/i);
  await assert.rejects(db.transaction(r.lease,request('write-query'),tx=>tx.query({sql:'DELETE FROM notes RETURNING id'},{maxRows:5,maxBytes:100})),/READONLY|readonly|read.only|write/i);
  const aged=await db.snapshot({maxAgeMs:10});await new Promise(resolve=>setTimeout(resolve,25));await assert.rejects(aged.query({sql:'SELECT 1'},{maxRows:1,maxBytes:100}),/EXPIRED/);await aged.close();



 }finally{await r.release();await db.close()}
});

test('PR04-T05 deadline invalidates callback and native progress cancellation releases actual SQL',async()=>{
 const {db,gate}=await fixture('cancel');const r=await gate.reserve({id:'cancel'});let late;
 try{
  const view=await db.snapshot({maxAgeMs:30000});const started=performance.now();let readClose;const reading=view.query({sql:'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n'},{maxRows:1,maxBytes:100});setTimeout(()=>{readClose=view.close()},20);await assert.rejects(reading,/interrupt/i);await readClose;assert.ok(performance.now()-started<2000,'snapshot close must interrupt native progress');assert.equal(await db.readRevision(),'0');

  await assert.rejects(db.transaction(r.lease,{...request('timeout'),maxDurationMs:10},async tx=>{late=tx;await new Promise(resolve=>setTimeout(resolve,60));await tx.execute({sql:'INSERT INTO notes VALUES (1,?)',parameters:['late']})}),/TIMEOUT|EXPIRED|interrupt/);
  await assert.rejects(late.execute({sql:'DELETE FROM notes'}),/EXPIRED/);
  await assert.rejects(db.transaction(r.lease,{...request('statements'),maxStatements:1},async tx=>{await tx.query({sql:'SELECT 1'},{maxRows:1,maxBytes:100});await tx.execute({sql:'INSERT INTO notes VALUES (1,?)',parameters:['over']})}),/BUDGET/);
  await assert.rejects(db.transaction(r.lease,request('nested'),()=>db.transaction(r.lease,request('inner'),async()=>{})),/NESTED/);
  const running=db.transaction(r.lease,request('cancel-native'),tx=>tx.query({sql:'WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n'},{maxRows:1,maxBytes:100}));
  setTimeout(()=>r.cancel('stop native'),20);
  await assert.rejects(running,/stop native|interrupt|cancel/i);
  await r.release();assert.equal(gate.snapshot().acceptedCommits,0);
  const next=await gate.reserve({id:'after-cancel'});await next.release();
 }finally{await r.release();await db.close()}
});

test('PR04-T02 COMMIT response loss is unknown; disk receipt resolves without replay',async()=>{
 const {db,gate}=await fixture('unknown');const r=await gate.reserve({id:'unknown'});let callbacks=0;
 const worker=db.rpc.worker;const original=worker.postMessage.bind(worker);let commitId;
 worker.postMessage=(message,...rest)=>{if(message.op==='commit')commitId=message.id;return original(message,...rest)};
 const listeners=worker.listeners('message');worker.removeAllListeners('message');
 worker.on('message',message=>{if(message.id===commitId){void worker.terminate();return}for(const listener of listeners)listener(message)});
 try{
  await assert.rejects(db.transaction(r.lease,request('lost'),async tx=>{callbacks++;await tx.execute({sql:'INSERT INTO notes VALUES (1,?)',parameters:['durable']})}),/COMMIT_UNKNOWN/);
  await r.release();const result=await db.probe('lost');assert.equal(result.status,'committed');assert.equal(result.receipt.revision,'1');assert.equal(callbacks,1);assert.equal(gate.snapshot().state,'halted');
 }finally{await r.release();await db.close()}
});

test('PR04-T02 worker loss before BEGIN or during SQL is probed after exit',async()=>{
 for(const phase of ['before','during']){
  const {db,gate}=await fixture(phase);const r=await gate.reserve({id:phase});
  try{
   if(phase==='before'){await db.rpc.worker.terminate();await db.rpc.exited}
   await assert.rejects(db.transaction(r.lease,request(phase),async tx=>{await tx.execute({sql:'INSERT INTO notes VALUES (1,?)',parameters:['rolled-back']});await db.rpc.worker.terminate();throw Error('worker lost during transaction')}),/WORKER_LOST|worker lost/);
   await r.release();assert.equal((await db.probe(phase)).status,'not-committed');
  }finally{await r.release();await db.close()}
 }
});

test('PR04-T05/T08 tiny binary views never clone their oversized backing stores',async()=>{
 const {db,gate}=await fixture('binary');const r=await gate.reserve({id:'binary'});let sentBytes=0;
 const post=db.rpc.worker.postMessage.bind(db.rpc.worker);db.rpc.worker.postMessage=(message,...rest)=>{if(message.op==='execute'&&message.args.statement.parameters?.[1] instanceof Uint8Array)sentBytes=message.args.statement.parameters[1].buffer.byteLength;return post(message,...rest)};
 try{
  const backing=new Uint8Array(8*1024*1024);backing.fill(7,9000,9032);const tiny=backing.subarray(9000,9032);
  await db.transaction(r.lease,request('binary-write'),tx=>tx.execute({sql:'INSERT INTO notes VALUES (?,?)',parameters:[1,tiny]}));assert.equal(sentBytes,32);
  const view=await db.snapshot({maxAgeMs:30000});const bytes=await view.readBlobRange({table:'notes',column:'body',rowid:1n,offset:4,length:8,maxBytes:8});assert.equal(bytes.buffer.byteLength,8);assert.deepEqual([...bytes],Array(8).fill(7));await view.close();
 }finally{await r.release();await db.close()}
});

test('PR04-T04 schema missing dependency, cycle and bad DDL halt private targets',async()=>{
 const cases=[[{owner:'a',from:0,to:1,after:['missing'],source:{version:'1'},statements:[]}],[{owner:'a',from:0,to:1,after:['b'],source:{version:'1'},statements:[]},{owner:'b',from:0,to:1,after:['a'],source:{version:'1'},statements:[]}],[{owner:'bad',from:0,to:1,after:[],source:{version:'1'},statements:[{sql:'CREATE TABLE broken ('}]}],[{owner:'foreign',from:0,to:1,after:[],source:{version:'1'},statements:[{sql:'CREATE TABLE child(id INTEGER PRIMARY KEY,parent INTEGER REFERENCES ghost(id))'}]}]];
 for(let i=0;i<cases.length;i++){
  const {db,gate}=await fixture('schema'+i);
  try{await assert.rejects(db.applyMigrations(cases[i]),/SCHEMA_|syntax|incomplete|foreign|FOREIGN|table/);assert.equal(db.diagnostics().halted,true);await assert.rejects(gate.reserve({id:'never-visible'}),/BUSY|HALT/)}finally{await db.close()}
 }
});

test('PR04-T09 reopen retains live data in diagnostic mode and rejects field-forged activation',async()=>{
 const {db,gate,directory}=await fixture('reopen');const first=await gate.reserve({id:'initial'});
 const frontier={codec:'step/1',value:0,logicalTime:0,completedCount:'0',pendingAcknowledgements:[]};
 await db.transaction(first.lease,request('frontier'),tx=>db.metadata.complete(tx,frontier,[]));await first.release();await db.close();
 assert.equal((await db.probe('frontier')).status,'unknown','closed service never opens a replacement worker');
 let reopened;const nextGate=new ExecutionGate({runId:'reopen',generation:'next',maxAcceptedCommits:5,readRevision:()=>reopened.readRevision()});
 reopened=await Database.open({runId:'reopen',directory,maxTransactionMs:20000},nextGate);
 try{
  assert.equal(reopened.diagnostics().diagnostic,true);const reservation=await nextGate.reserve({id:'blocked'});
  await assert.rejects(reopened.transaction(reservation.lease,request('must-not-write'),async()=>{}),/DIAGNOSTIC/);await reservation.release();
  const snapshot=await reopened.snapshot({maxAgeMs:30000});assert.deepEqual(await reopened.metadata.read(snapshot),frontier);const schema=snapshot.schema;await snapshot.close();
  const sealed=await nextGate.seal();
  await assert.rejects(reopened.activateFromCompletePoint({...sealed},{id:'unverified',runId:'reopen',revision:'1',frontier,schema}),/FENCE_PUBLICATION/);
  await assert.rejects(reopened.activateFromCompletePoint(sealed,{id:'wrong',runId:'reopen',revision:'0',frontier,schema}),/MISMATCH/);
  await assert.rejects(reopened.activateFromCompletePoint(sealed,{id:'wrong-frontier',runId:'reopen',revision:'1',frontier:{...frontier,completedCount:'99'},schema}),/MISMATCH/);
  assert.equal(reopened.diagnostics().diagnostic,true);assert.equal(nextGate.snapshot().state,'sealed');
 }finally{await reopened.close()}
});
