import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Worker} from 'node:worker_threads';
import {openConnection} from '../.build/sqlite-native/src/index.mjs';

// 写事务遇到其他连接短暂持锁时须在 timeoutMs 内等待，而不是立刻 SQLITE_BUSY。
const holder=`const {workerData,parentPort}=require('node:worker_threads');
import(workerData.module).then(({openConnection})=>{const db=openConnection({path:workerData.path});db.begin();db.execute('INSERT INTO t VALUES(1)');parentPort.postMessage('locked');
setTimeout(()=>{db.commit();db.close();parentPort.postMessage('released')},300)})`;

test('sqlite-native：写事务在 timeoutMs 内等待其他连接释放写锁',async()=>{
  const home=await mkdtemp(join(tmpdir(),'sqlite-busy-')),path=join(home,'a.sqlite');
  try {
    const setup=openConnection({path});setup.execSchema('CREATE TABLE t(v INTEGER)');setup.close();
    const worker=new Worker(holder,{eval:true,workerData:{path,module:new URL('../.build/sqlite-native/src/index.mjs',import.meta.url).href}});
    const released=new Promise(resolve=>worker.on('message',message=>{if(message==='released')resolve()}));
    await new Promise(resolve=>worker.once('message',resolve));
    const db=openConnection({path,timeoutMs:5000});
    const started=Date.now();db.begin();db.execute('INSERT INTO t VALUES(2)');db.commit();
    assert(Date.now()-started>=200,'应等待持锁方释放');
    assert.equal(db.query('SELECT count(*) AS n FROM t',[],{maxRows:1,maxBytes:1024})[0].n,2n);
    db.close();await released;await worker.terminate();
  } finally {await rm(home,{recursive:true,force:true})}
});

test('sqlite-native：持锁超过 timeoutMs 时以 SQLITE_BUSY 失败',async()=>{
  const home=await mkdtemp(join(tmpdir(),'sqlite-busy-')),path=join(home,'a.sqlite');
  try {
    const setup=openConnection({path});setup.execSchema('CREATE TABLE t(v INTEGER)');setup.close();
    const a=openConnection({path}),b=openConnection({path,timeoutMs:100});
    a.begin();a.execute('INSERT INTO t VALUES(1)');
    assert.throws(()=>b.begin(),error=>error.code==='SQLITE_BUSY');
    a.commit();a.close();b.close();
  } finally {await rm(home,{recursive:true,force:true})}
});
