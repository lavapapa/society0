import assert from 'node:assert/strict';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {openConnection,sqliteMemory,configureHeapLimit} from '../.build/sqlite-native/src/index.mjs';
const count=Number(process.argv[2]);
if(count){
 const root=join(tmpdir(),'sqlite-native-');const home=mkdtempSync(root+'memory-');
 configureHeapLimit(8*1024*1024);
 const d=openConnection({path:home+'/source',maxInlineBytes:8192,maxInputBytes:512*1024,timeoutMs:60000}),target=openConnection({path:home+'/target',maxInlineBytes:8192,timeoutMs:60000});
 try{
 for(const c of [d,target])c.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,b BLOB);');d.startSession(['t']);d.begin({maxDurationMs:60000,maxCapturedBytes:2*1024*1024});
 const block=new Uint8Array(4096);for(let off=0;off<count;off+=64){const rows=Array.from({length:Math.min(64,count-off)},(_,i)=>[BigInt(off+i),block]);d.executeMany('INSERT INTO t VALUES(?,?)',rows,{maxRows:64,maxInputBytes:512*1024});}
 d.commit();const capture=d.state().sessionBytes,before=sqliteMemory();const segment=d.exportSegment(home+'/segment',{chunkBytes:1024,maxBytes:16*1024*1024});const after=sqliteMemory();
 const apply=target.applySegment(home+'/segment',{chunkBytes:1024,maxBytes:16*1024*1024});assert.equal(target.query('SELECT count(*) AS n FROM t')[0].n,BigInt(count));assert.ok(segment.maxIoChunk<=1024);assert.ok(apply.maxIoChunk<=1024);assert.ok(sqliteMemory().peak<8*1024*1024);
 console.log(JSON.stringify({count,capture,segment,apply,before,after,final:sqliteMemory(),rssPeakKiB:process.resourceUsage().maxRSS,nodeMemory:process.memoryUsage()}));
 }finally{d.close();target.close();configureHeapLimit(0);}
}
