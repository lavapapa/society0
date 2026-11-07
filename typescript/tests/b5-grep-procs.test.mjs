import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';
import {SEARCH_BATCH,searchProcesses} from '../.build/core/dist/exploration/search.js';

const dir='/public/market/prices';
const total=1000;

test('1000个候选文件的rg进程数不超过参数批数',async()=>{
 const run=await setup();try{
  await run.tx('b5-procs-seed',async tx=>{
   const at=BigInt(tx.baseRevision)+1n;
   const rows=[],files=[];
   for(let i=0;i<total;i++){
    const name=`f${String(i).padStart(5,'0')}.jsonl`,path=`${dir}/${name}`,line=JSON.stringify({id:'p'+i,at:'2026-11-01',price:'1.00',note:'processcheck row '+i});
    rows.push([path,'p'+i,'2026-11-01 0000'+String(i).padStart(5,'0'),at,null,'all',Buffer.from(line)]);
    files.push([path,dir,at,table.path,1,Buffer.byteLength(line)+1,'["all"]',name,'jsonl']);
   }
   for(let offset=0;offset<rows.length;offset+=128)await tx.executeMany('INSERT INTO public_rows(file,row_key,sort_key,rev_from,rev_to,audience,line) VALUES(?,?,?,?,?,?,?)',rows.slice(offset,offset+128),{maxRows:128,maxInputBytes:1<<20});
   for(let offset=0;offset<files.length;offset+=256)await tx.executeMany('INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,NULL,?,?,?,?,?,?)',files.slice(offset,offset+256),{maxRows:256,maxInputBytes:1<<20});
  });
  await run.tx('b5-procs-rebuild',tx=>run.ctx.public.rebuildSearch(tx));
  await run.openMoment('m');
  const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-procs'));
  try{
   const before=searchProcesses.started;
   const result=await session.value.grep({root:'/public',pattern:'processcheck',mode:'literal',page:{maxItems:2000,maxResponseBytes:1<<24}});
   const started=searchProcesses.started-before;
   assert.equal(result.total,String(total));
   assert.ok(started<=Math.ceil(total/SEARCH_BATCH),`rg 进程 ${started} 超过批数 ${Math.ceil(total/SEARCH_BATCH)}`);
   console.log(JSON.stringify({case:'B5-grep-procs',files:total,processes:started,batches:Math.ceil(total/SEARCH_BATCH)}));
  }finally{await session.release()}
 }finally{await run.close()}
});
