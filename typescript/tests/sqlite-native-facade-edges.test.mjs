import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,basename} from 'node:path';
import {openConnection} from '../.build/sqlite-native/src/index.mjs';
const argument={code:'SQLITE_ARGUMENT'},state={code:'SQLITE_STATE'};
async function directory(t){
 const root=await mkdtemp(join(tmpdir(),'new6-facade-')),intended=await realpath(root);
 t.after(async()=>{
  assert.equal(await realpath(root),intended);
  assert.equal(dirname(intended),await realpath(tmpdir()));assert.ok(basename(intended).startsWith('new6-facade-'));
  await rm(intended,{recursive:true});
 });
 return root;
}
test('NEW-6-Q01 public facade validates constructor and SQL values before native dispatch',async t=>{
 const root=await directory(t);
 for(const options of [{path:1},{path:'a\0b'},{path:join(root,'db'),maxInlineBytes:1023},{path:join(root,'db'),control:[]},{path:join(root,'db'),control:new Int32Array(1)},{path:join(root,'db'),control:new Int32Array(new SharedArrayBuffer(8))}])assert.throws(()=>openConnection(options),argument);
 const d=openConnection({path:join(root,'db')});
 try{
  for(const sql of [1,'a\0b'])assert.throws(()=>d.query(sql),argument);
  for(const value of [true,{},NaN,Infinity,1n<<63n,-(1n<<63n)-1n])assert.throws(()=>d.query('SELECT ?',[value]),argument);
  assert.throws(()=>d.query('SELECT 1',{}),argument);
  d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v)');d.begin();
  assert.throws(()=>d.begin(),{code:'SQLITE_NESTED_TRANSACTION'});
  for(const rows of [null,[[1]],{}])assert.throws(()=>d.executeMany('INSERT INTO t VALUES(?)',rows,{maxRows:0}),{code:'SQLITE_INPUT_LIMIT'});
  assert.throws(()=>d.executeMany('INSERT INTO t VALUES(?)',[{}]),argument);
  assert.throws(()=>d.executeMany('SELECT 1',[],{maxInputBytes:1}),{code:'SQLITE_INPUT_LIMIT'});
  d.execute('INSERT INTO t VALUES(?,?)',[1n,null]);d.rollback();
  assert.throws(()=>d.rollback(),state);assert.throws(()=>d.commit(),state);
  assert.throws(()=>d.query('SELECT 1',[],{maxRows:-1}),argument);
  for(const opts of [{table:1,column:'v',rowid:1n,offset:0,length:1},{table:'t',column:'v',rowid:1,offset:0,length:1}])assert.throws(()=>d.readBlobRange(opts),argument);
 }finally{d.close()}
 assert.throws(()=>d.rollback(),{code:'SQLITE_CLOSED'});assert.equal(d.state().closed,true);
});
test('NEW-6-Q02 session validation preserves state priority and snapshot/export boundaries',async t=>{
 const root=await directory(t),path=join(root,'world'),d=openConnection({path});
 try{
  d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v)');
  for(const tables of [null,[],['t','t'],[1],[''],['a\0b']])assert.throws(()=>d.startSession(tables),argument);
  assert.throws(()=>d.exportSegment(join(root,'idle')),state);assert.throws(()=>d.endSnapshot(),state);
  d.startSession(['t']);assert.throws(()=>d.startSession(null),state);assert.throws(()=>d.execSchema('SELECT 1'),state);
  d.begin();assert.throws(()=>d.startSession(null),state);d.execute('INSERT INTO t VALUES(?,?)',[1n,'one']);d.commit();
  assert.throws(()=>d.begin(),{code:'SQLITE_SEGMENT_PENDING'});assert.throws(()=>d.retireSession(),state);
  assert.throws(()=>d.exportSegment(join(root,'zero'),{chunkBytes:0}),argument);
  d.exportSegment(join(root,'segment'));d.startSession(['t']);d.retireSession();
  assert.throws(()=>d.applySegment(join(root,'segment'),{chunkBytes:0}),argument);
  assert.throws(()=>d.backupTo(join(root,'zero-backup'),{pagesPerStep:0}),argument);
  const s=openConnection({path,readOnly:true});
  try{
   assert.throws(()=>s.begin(),{code:'SQLITE_READONLY'});s.beginSnapshot();assert.throws(()=>s.beginSnapshot(),state);s.endSnapshot();s.endSnapshot();
  }finally{s.close()}
  assert.throws(()=>s.endSnapshot(),{code:'SQLITE_CLOSED'});
  const expired=openConnection({path,readOnly:true});
  try{expired.beginSnapshot({maxAgeMs:0});assert.throws(()=>expired.query('SELECT 1'),{code:'SQLITE_SNAPSHOT_EXPIRED'});expired.endSnapshot()}finally{expired.close()}
 }finally{d.close()}
});
