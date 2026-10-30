import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,basename} from 'node:path';
import {openConnection} from '../.build/sqlite-native/src/index.mjs';
import {Database} from '../.build/core/dist/sqlite/database.js';
import {ExecutionGate} from '../.build/core/dist/execution-fence/state.js';
const high=String.fromCharCode(0xd800),low=String.fromCharCode(0xdc00),reversed=low+high;
async function directory(t){
 const root=await mkdtemp(join(tmpdir(),'new6-'));const intended=await realpath(root);
 t.after(async()=>{
  assert.equal(await realpath(root),intended);
  assert.equal(dirname(intended),await realpath(tmpdir()));assert.ok(basename(intended).startsWith('new6-'));
  await rm(intended,{recursive:true});
 });
 return root;
}
const malformed=at=>error=>{assert.equal(error.code,'SQLITE_TEXT_NOT_WELL_FORMED');assert.match(error.message,at);return true};
const argument=error=>{assert.equal(error.code,'SQLITE_ARGUMENT');return true};
const count=d=>d.query('SELECT count(*) AS n FROM t')[0].n;

test('NEW-6-T01 lone surrogate parameter is rejected before binding',async t=>{
 const root=await directory(t),d=openConnection({path:join(root,'db')});
 try{
  d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT)');
  for(const value of ['a'+high+'b','a'+low+'b',reversed]){
   d.begin();assert.throws(()=>d.execute('INSERT INTO t(v) VALUES(?)',[value]),malformed(/#0/));
   assert.equal(d.state().failed,false);d.rollback();assert.equal(count(d),0n);
  }
 }finally{d.close()}
});

test('NEW-6-T02 lone surrogates in SQL text, query parameters and table names are rejected',async t=>{
 const root=await directory(t),d=openConnection({path:join(root,'db')});
 try{
  assert.throws(()=>d.execSchema(`CREATE TABLE "bad${high}"(id INTEGER PRIMARY KEY)`),malformed(/SQL/));
  d.execSchema('CREATE TABLE "t�"(id INTEGER PRIMARY KEY,v TEXT)');
  assert.throws(()=>d.query(`SELECT '${low}' AS v`),malformed(/SQL/));
  assert.throws(()=>d.query('SELECT ? AS v',['x'+high]),malformed(/#0/));
  assert.throws(()=>d.query('SELECT ? AS v',[reversed]),malformed(/#0/));
  assert.throws(()=>d.startSession(['t'+high]),argument);
  assert.equal(d.state().session,'none');d.startSession(['t�']);d.retireSession();
 }finally{d.close()}
});

test('NEW-6-T02 BLOB identifiers reject malformed text without aliasing a valid replacement character',async t=>{
 const root=await directory(t),d=openConnection({path:join(root,'db')});
 try{
  d.execSchema('CREATE TABLE "table�"(id INTEGER PRIMARY KEY,"blob�" BLOB)');
  d.begin();d.execute('INSERT INTO "table�" VALUES(?,?)',[1n,new Uint8Array([1,2])]);d.commit();
  const request={table:'table�',column:'blob�',rowid:1n,offset:0,length:2,maxBytes:2};
  assert.throws(()=>d.readBlobRange({...request,table:'table'+high}),argument);
  assert.throws(()=>d.readBlobRange({...request,column:'blob'+low}),argument);
  assert.deepEqual([...d.readBlobRange(request)],[1,2]);
 }finally{d.close()}
});

test('NEW-6-T02 native filesystem paths reject malformed text before creating aliases or poisoning an export',async t=>{
 const root=await directory(t),d=openConnection({path:join(root,'db')});let unexpected;
 try{
  try{assert.throws(()=>{unexpected=openConnection({path:join(root,'bad-'+high+'.db')})},argument)}finally{unexpected?.close()}
  d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT)');
  const before=(await readdir(root)).sort();
  assert.throws(()=>d.backupTo(join(root,'bad-'+low+'.backup')),argument);
  assert.throws(()=>d.applySegment(join(root,'bad-'+high+'.segment')),argument);
  assert.deepEqual((await readdir(root)).sort(),before);
  d.startSession(['t']);d.begin();d.execute('INSERT INTO t(v) VALUES(?)',['kept']);d.commit();
  assert.throws(()=>d.exportSegment(join(root,'bad-'+high+'.segment')),argument);
  assert.equal(d.state().dirty,false);assert.equal(d.state().pendingSegment,true);
  assert.ok(d.exportSegment(join(root,'good.segment')).bytes>0);
  assert.equal((await readdir(root)).some(name=>name.includes('�')),false);
 }finally{d.close()}
});

test('NEW-6-T03 executeMany rejects the whole batch before any row executes',async t=>{
 const root=await directory(t),d=openConnection({path:join(root,'db')});
 try{
  d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT)');d.begin();
  assert.throws(()=>d.executeMany('INSERT INTO t(v) VALUES(?)',[['first'],[high],['third']]),malformed(/row 1 #0/));
  assert.equal(count(d),0n);assert.equal(d.state().failed,false);d.rollback();assert.equal(count(d),0n);
 }finally{d.close()}
});

test('NEW-6-T04 well-formed astral, CJK, NUL and decomposed text round-trips byte-exactly',async t=>{
 const root=await directory(t),d=openConnection({path:join(root,'db')});
 const values=['𝒜中é😀',String.fromCodePoint(0x10000),'e'+String.fromCharCode(0x301),'a'+String.fromCharCode(0)+'b','�',String.fromCharCode(0xffff)];
 try{
  d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT)');d.begin();
  d.executeMany('INSERT INTO t VALUES(?,?)',values.map((value,i)=>[BigInt(i),value]));d.commit();
  const rows=d.query('SELECT v FROM t ORDER BY id');assert.equal(rows.length,values.length);
  for(let i=0;i<values.length;i++){assert.equal(rows[i].v,values[i]);assert.equal(Buffer.compare(Buffer.from(rows[i].v),Buffer.from(values[i])),0)}
 }finally{d.close()}
});

test('NEW-6-T05 transaction body hitting a lone surrogate settles not-committed without halt',async t=>{
 const root=await directory(t);let db;
 const gate=new ExecutionGate({runId:'new6',generation:'g1',maxAcceptedCommits:8,readRevision:()=>db.readRevision()});
 db=await Database.open({directory:root,runId:'new6',maxTransactionMs:20000},gate);let reservation;
 const request=operationId=>({operationId,maxDurationMs:15000,maxStatements:20,maxInputBytes:65536,maxChangedRows:100,maxCapturedBytes:1048576});
 try{
  await db.applyMigrations([{owner:'new6-notes',from:0,to:1,after:[],source:{package:'new6-notes',version:'1'},statements:[{sql:'CREATE TABLE notes(id INTEGER PRIMARY KEY,v TEXT)'}]}]);
  reservation=await gate.reserve({id:'new6-proposal'});
  await assert.rejects(db.transaction(reservation.lease,request('new6-bad'),tx=>tx.execute({sql:'INSERT INTO notes VALUES(?,?)',parameters:[1n,'a'+high+'b']})),malformed(/#1/));
  assert.equal((await db.probe('new6-bad')).status,'not-committed');assert.equal(db.diagnostics().halted,false);assert.equal(await db.readRevision(),'0');
  const result=await db.transaction(reservation.lease,request('new6-good'),tx=>tx.execute({sql:'INSERT INTO notes VALUES(?,?)',parameters:[2n,'正常😀']}));
  assert.equal(result.receipt.revision,'1');assert.equal((await db.probe('new6-good')).status,'committed');
  const snapshot=await db.snapshot({maxAgeMs:30000});try{assert.deepEqual(await snapshot.query({sql:'SELECT v FROM notes'},{maxRows:2,maxBytes:1024}),[{v:'正常😀'}])}finally{await snapshot.close()}
 }finally{await reservation?.release();await db.close()}
});
