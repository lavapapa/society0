import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Worker} from 'node:worker_threads';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import {openConnection,createInterruptControl,configureHeapLimit,sqliteMemory} from '../.build/sqlite-native/src/index.mjs';
const root=join(tmpdir(),'sqlite-native-');const dir=()=>mkdtempSync(root+'boundary-');
const one=d=>d.query('SELECT count(*) AS n FROM t')[0].n;
test('输入/输出预算、单statement、领域SQL权限和过期事务',()=>{
 const d=openConnection({path:dir()+'/db',maxInlineBytes:65536,maxInputBytes:4096});
 try{
 d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,b BLOB);');d.begin();
 assert.throws(()=>d.executeMany('INSERT INTO t VALUES(?,?)',[[1n,new Uint8Array(8192)]],{maxRows:1,maxInputBytes:8192}),{code:'SQLITE_INPUT_LIMIT'});assert.equal(one(d),0n);
 assert.throws(()=>d.execute('COMMIT'));d.rollback();
 d.begin();d.executeMany('INSERT INTO t VALUES(?,?)',[[1n,new Uint8Array(256)],[2n,new Uint8Array(256)]]);d.commit();
 assert.throws(()=>d.query('SELECT * FROM t',[],{maxRows:1,maxBytes:1024}),{code:'SQLITE_RESULT_LIMIT'});
 assert.throws(()=>d.query('SELECT * FROM t',[],{maxRows:10,maxBytes:100}),{code:'SQLITE_RESULT_LIMIT'});
 assert.throws(()=>d.query('DELETE FROM t'));assert.equal(one(d),2n);
 assert.throws(()=>d.query('SELECT id,id FROM t'),{code:'SQLITE_ARGUMENT'});
 d.begin();assert.throws(()=>d.execute('INSERT INTO t VALUES(3,zeroblob(100000))'),{code:'SQLITE_TOOBIG'});assert.throws(()=>d.commit(),{code:'SQLITE_TRANSACTION_FAILED'});d.rollback();
 assert.throws(()=>d.execute('DELETE FROM t'),{code:'SQLITE_STATE'});
 assert.throws(()=>d.begin({maxDurationMs:0}),{code:'SQLITE_INTERRUPT'});
 }finally{d.close();}
});
test('BLOB范围属于固定snapshot，期限结束后拒绝借用',async()=>{
 const home=dir(),d=openConnection({path:home+'/db',maxInlineBytes:8*1024*1024});let s;
 try{
 d.execSchema("CREATE TABLE t(id INTEGER PRIMARY KEY,b BLOB);INSERT INTO t VALUES(1,CAST(X'01020304'||zeroblob(4194296)||X'05060708' AS BLOB));");
 }catch(error){d.close();throw error;}
 try{
 s=openConnection({path:home+'/db',readOnly:true});s.beginSnapshot({maxAgeMs:60000});
 d.begin();d.execute('UPDATE t SET b=? WHERE id=?',[new Uint8Array([9,8,7]),1n]);d.commit();
 assert.deepEqual([...s.readBlobRange({table:'t',column:'b',rowid:1n,offset:4194300,length:4,maxBytes:4})],[5,6,7,8]);
 assert.throws(()=>s.readBlobRange({table:'t',column:'b',rowid:1n,offset:0,length:5,maxBytes:4}),{code:'SQLITE_RESULT_LIMIT'});
 s.endSnapshot();assert.throws(()=>s.query('SELECT 1'),{code:'SQLITE_SNAPSHOT_CLOSED'});s.close();
 s=openConnection({path:home+'/db',readOnly:true});s.beginSnapshot({maxAgeMs:5});await delay(15);assert.throws(()=>s.query('SELECT 1'),{code:'SQLITE_SNAPSHOT_EXPIRED'});assert.equal(s.state().autocommit,true);
 }finally{s?.close();d.close();}
});
test('触发器暂停、外键级联和冲突/截断apply原子回滚',()=>{
 const home=dir(),d=openConnection({path:home+'/source'}),t=openConnection({path:home+'/target'}),bad=openConnection({path:home+'/bad'});
 const ddl=`CREATE TABLE zparent(id INTEGER PRIMARY KEY);CREATE TABLE achild(id INTEGER PRIMARY KEY,p REFERENCES zparent(id) ON DELETE CASCADE);CREATE TABLE audit(id INTEGER PRIMARY KEY,v INTEGER);CREATE TRIGGER ins AFTER INSERT ON zparent BEGIN INSERT INTO audit(v) VALUES(new.id);END;`;
 try{
 for(const c of [d,t,bad])c.execSchema(ddl);
 d.startSession(['zparent','achild','audit']);d.begin();d.execute('INSERT INTO zparent VALUES(?)',[1n]);d.execute('INSERT INTO achild VALUES(?,?)',[1n,1n]);d.commit();d.exportSegment(home+'/s0');
 t.applySegment(home+'/s0');assert.equal(t.query('SELECT count(*) AS n FROM audit')[0].n,1n);
 d.startSession(['zparent','achild','audit']);d.begin();d.execute('DELETE FROM zparent');d.commit();d.exportSegment(home+'/s1');t.applySegment(home+'/s1');assert.equal(t.query('SELECT count(*) AS n FROM achild')[0].n,0n);
 t.begin();t.execute('INSERT INTO zparent VALUES(?)',[2n]);t.commit();assert.equal(t.query('SELECT count(*) AS n FROM audit')[0].n,2n);
 assert.throws(()=>t.applySegment(home+'/s0'),{code:'SQLITE_ABORT'});assert.equal(t.query('SELECT count(*) AS n FROM zparent')[0].n,1n);
 const bytes=readFileSync(home+'/s0');writeFileSync(home+'/truncated',bytes.subarray(0,bytes.length-2));assert.throws(()=>bad.applySegment(home+'/truncated'));assert.equal(bad.query('SELECT count(*) AS n FROM zparent')[0].n,0n);
 bad.begin();bad.execute('INSERT INTO zparent VALUES(?)',[3n]);bad.commit();assert.equal(bad.query('SELECT count(*) AS n FROM audit')[0].n,1n);
 }finally{d.close();t.close();bad.close();}
});
test('导出失败退役Session并保留dirty，备份分页可重开',()=>{
 const home=dir(),d=openConnection({path:home+'/db'});
 try{
 d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,b TEXT);');d.startSession(['t']);d.begin();d.execute('INSERT INTO t VALUES(?,?)',[1n,'body']);d.commit();
 assert.throws(()=>d.exportSegment(home+'/tiny',{maxBytes:1}),{code:'SQLITE_TOOBIG'});assert.equal(d.state().dirty,true);assert.equal(d.state().session,'retired');assert.throws(()=>d.begin(),{code:'SQLITE_DIRTY'});
 d.backupTo(home+'/copy',{pagesPerStep:1});const copy=openConnection({path:home+'/copy',readOnly:true});assert.equal(copy.query('SELECT b FROM t')[0].b,'body');copy.close();
 }finally{d.close();}
});
test('Session schema拒绝无主键/可空主键和虚拟表',()=>{
 const d=openConnection({path:dir()+'/db'});try{d.execSchema('CREATE TABLE plain(v);CREATE TABLE nullable(id TEXT PRIMARY KEY,v);CREATE VIRTUAL TABLE virtual USING fts5(v);');for(const table of ['plain','nullable','virtual','absent'])assert.throws(()=>d.startSession([table]));}finally{d.close();}
});
test('1MiB原生硬上限导致级联OOM，Session退役且回滚可诊断',()=>{
 const d=openConnection({path:dir()+'/db',maxCapturedBytes:32*1024*1024,maxInputBytes:4096,timeoutMs:30000});
 try{
 d.execSchema('CREATE TABLE parent(id INTEGER PRIMARY KEY);CREATE TABLE t(id INTEGER PRIMARY KEY,p REFERENCES parent(id) ON DELETE CASCADE,b BLOB);INSERT INTO parent VALUES(1);WITH RECURSIVE n(x) AS(VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<512) INSERT INTO t SELECT x,1,zeroblob(4096) FROM n;');
 d.startSession(['parent','t']);configureHeapLimit(1024*1024);d.begin({maxDurationMs:5000});assert.throws(()=>d.execute('DELETE FROM parent'),{code:'SQLITE_NOMEM'});assert.equal(d.state().session,'retired');assert.equal(d.state().lastError.sqliteCode,7);assert.ok(d.state().retiredSessionBytes>0);
 configureHeapLimit(0);assert.throws(()=>d.commit(),{code:'SQLITE_TRANSACTION_FAILED'});d.rollback();assert.equal(one(d),512n);assert.equal(d.state().session,'none');d.startSession(['parent','t']);d.begin({maxDurationMs:5000});d.execute('DELETE FROM t WHERE id=?',[1n]);d.commit();d.exportSegment(dir()+'/recovered');assert.equal(one(d),511n);
 }finally{configureHeapLimit(0);d.close();}
});
test('宿主在worker阻塞于原生SQL时直接SAB取消，结算后再释放worker',async()=>{
 const control=createInterruptControl(),worker=new Worker(new URL('./sqlite-native-worker.mjs',import.meta.url),{workerData:{path:dir()+'/db',control,timeoutMs:60000}});
 const exit=once(worker,'exit');
 try{
 await once(worker,'message');let settled=false;let startedResolve;const started=new Promise(r=>startedResolve=r);
 const outcome=new Promise((resolve,reject)=>{worker.on('message',m=>{if(m.event==='started'&&m.id===1)startedResolve();if(m.id===1&&!m.event){settled=true;resolve(m);}});worker.once('error',reject);});
 worker.postMessage({id:1,method:'query',args:['WITH RECURSIVE n(x) AS(VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) AS n FROM n']});await started;await delay(25);assert.equal(settled,false);
 const at=performance.now();Atomics.store(control,0,1);const result=await outcome;const interruptMs=performance.now()-at;
 assert.equal(result.error.code,'SQLITE_INTERRUPT');assert.equal(result.state.stopReason,'cancelled');assert.ok(interruptMs<2000);console.log(JSON.stringify({nativeInterruptMs:interruptMs,physicallySettled:true}));
 Atomics.store(control,0,0);const next=new Promise(resolve=>{worker.on('message',m=>{if(m.id===2&&!m.event)resolve(m);});});worker.postMessage({id:2,method:'query',args:['SELECT 42 AS n']});assert.equal((await next).value[0].n,42n);
 worker.postMessage({id:3,method:'close'});assert.deepEqual(await exit,[0]);
 }catch(error){Atomics.store(control,0,1);await worker.terminate();throw error;}
});

test('物化大值OOM保留事务身份，回滚后重新捕获',()=>{
 const home=dir(),d=openConnection({path:home+'/db',maxInlineBytes:32*1024*1024});
 try{
 d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,b BLOB);INSERT INTO t VALUES(1,zeroblob(1024));');d.startSession(['t']);d.begin();configureHeapLimit(1024*1024);
 assert.throws(()=>d.execute('UPDATE t SET b=CAST(zeroblob(8388608)||zeroblob(8388608) AS BLOB)'),{code:'SQLITE_NOMEM'});assert.equal(d.state().transaction,true);assert.equal(d.state().session,'retired');configureHeapLimit(0);d.rollback();assert.equal(d.query('SELECT length(b) AS n FROM t')[0].n,1024n);assert.equal(d.state().session,'none');
 }finally{configureHeapLimit(0);d.close();}
});
test('取消apply完整回滚并恢复触发器，取消export进入dirty',()=>{
 const home=dir(),source=openConnection({path:home+'/s'}),target=openConnection({path:home+'/t'});
 try{
 for(const d of [source,target])d.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT);');source.startSession(['t']);source.begin();source.execute('INSERT INTO t VALUES(?,?)',[1n,'body']);source.commit();source.exportSegment(home+'/segment');
 Atomics.store(target.control,0,1);assert.throws(()=>target.applySegment(home+'/segment'),{code:'SQLITE_INTERRUPT'});Atomics.store(target.control,0,0);assert.equal(one(target),0n);target.applySegment(home+'/segment');assert.equal(one(target),1n);
 source.startSession(['t']);source.begin();source.execute('UPDATE t SET v=?',['next']);source.commit();Atomics.store(source.control,0,1);assert.throws(()=>source.exportSegment(home+'/cancel'),{code:'SQLITE_INTERRUPT'});assert.equal(source.state().dirty,true);assert.equal(source.state().session,'retired');
 }finally{source.close();target.close();}
});
