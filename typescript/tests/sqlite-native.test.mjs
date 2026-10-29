import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {openConnection} from '../packages/sqlite-native/src/index.mjs';
const root=fileURLToPath(new URL('../packages/sqlite-native/build/',import.meta.url));mkdirSync(root,{recursive:true});
const dir=()=>mkdtempSync(root+'test-');
test('同连接参数、JOIN和两个参与者回滚',()=>{
 const d=openConnection({path:dir()+'/world.db'});
 try{
 d.execSchema('CREATE TABLE actor(id INTEGER PRIMARY KEY,name TEXT);CREATE TABLE note(id INTEGER PRIMARY KEY,actor INTEGER REFERENCES actor(id),body BLOB);CREATE INDEX note_actor ON note(actor);');
 d.begin();d.execute('INSERT INTO actor VALUES(?,?)',[9223372036854775807n,'中\0文']);d.execute('INSERT INTO note VALUES(?,?,?)',[1n,9223372036854775807n,new Uint8Array([0,255,1])]);d.commit();
 const rows=d.query('SELECT a.id,a.name,n.body FROM actor a JOIN note n ON n.actor=a.id',[],{maxRows:2,maxBytes:1024});
 assert.equal(rows[0].id,9223372036854775807n);assert.equal(rows[0].name,'中\0文');assert.deepEqual([...rows[0].body],[0,255,1]);
 d.begin();d.execute('INSERT INTO actor VALUES(?,?)',[2n,'second']);assert.throws(()=>d.execute('INSERT INTO note VALUES(?,?,?)',[2n,99n,new Uint8Array()]));d.rollback();assert.equal(d.query('SELECT count(*) AS n FROM actor')[0].n,1n);
 assert.throws(()=>d.query('SELECT 1;SELECT 2'));assert.equal(d.query('SELECT 1 AS n; -- trailing comment\n /* allowed */')[0].n,1n);
 const unusual=d.query("SELECT X'FF' AS __proto__,'' AS empty,NULL AS nil,1.5 AS real")[0];assert.equal(Object.hasOwn(unusual,'__proto__'),true);assert.deepEqual([...unusual.__proto__],[255]);assert.equal(unusual.empty,'');assert.equal(unusual.nil,null);assert.equal(unusual.real,1.5);
 d.begin();d.execute('INSERT INTO actor VALUES(?,?)',[3n,'rollback']);assert.throws(()=>d.execute('SAVEPOINT escape'));d.rollback();assert.equal(d.query('SELECT count(*) AS n FROM actor')[0].n,1n);
 }finally{d.close();d.close();}
 assert.throws(()=>d.query('SELECT 1'),{code:'SQLITE_CLOSED'});
});
test('事务分段Session原生导出和恢复',()=>{
 const home=dir(),d=openConnection({path:home+'/source.db'}),t=openConnection({path:home+'/target.db'});
 try{
 for(const x of [d,t])x.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT);');
 d.startSession(['t']);d.begin();d.executeMany('INSERT INTO t VALUES(?,?)',[[1n,'one'],[2n,'two']],{maxRows:2,maxInputBytes:1024});d.commit();
 assert.throws(()=>d.begin(),{code:'SQLITE_SEGMENT_PENDING'});
 const segment=d.exportSegment(home+'/s0.bin',{chunkBytes:1024});assert.ok(segment.bytes>0);assert.ok(segment.maxIoChunk<=1024);
 t.applySegment(home+'/s0.bin',{chunkBytes:1024});assert.deepEqual(t.query('SELECT * FROM t ORDER BY id'),d.query('SELECT * FROM t ORDER BY id'));
 d.startSession(['t']);d.begin();d.execute('UPDATE t SET v=? WHERE id=?',['three',1n]);d.commit();d.exportSegment(home+'/s1.bin');t.applySegment(home+'/s1.bin');assert.equal(t.query('SELECT v FROM t WHERE id=1')[0].v,'three');
 }finally{d.close();t.close();}
});

test('原生foreign_key_check定位违例，managed SQL继续拒绝PRAGMA',()=>{
 const d=openConnection({path:dir()+'/db'});
 try{
 d.execSchema('PRAGMA foreign_keys=OFF;CREATE TABLE parent(id INTEGER PRIMARY KEY);CREATE TABLE child(id INTEGER PRIMARY KEY,p REFERENCES parent(id));INSERT INTO child VALUES(1,99);PRAGMA foreign_keys=ON;');
 assert.throws(()=>d.checkForeignKeys(),e=>e.code==='SQLITE_CONSTRAINT'&&e.sqliteCode===19&&e.message.includes('child -> parent')&&e.message.includes('rowid=1'));
 assert.throws(()=>d.query('PRAGMA foreign_key_check'),{code:'SQLITE_AUTH'});
 d.begin();d.execute('INSERT INTO parent VALUES(?)',[99n]);d.commit();assert.equal(d.checkForeignKeys(),undefined);
 }finally{d.close();}
});
