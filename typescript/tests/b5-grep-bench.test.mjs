import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';
import {NativeSearch} from '../.build/core/dist/exploration/search.js';

const dir='/public/market/prices',files=5,perFile=20000,chunk=5000;
const page={maxItems:4000,maxResponseBytes:16*1024*1024};

test('grep冷/热与正则基线',async()=>{
 const run=await setup();try{
  let bytes=0;
  for(let f=0;f<files;f++){
   const name=`f${String(f).padStart(5,'0')}.jsonl`,path=`${dir}/${name}`;
   let fileBytes=0;
   for(let start=0;start<perFile;start+=chunk){
    const count=Math.min(chunk,perFile-start);
    await run.tx(`b5-bench-seed-${f}-${start}`,async tx=>{
     const at=BigInt(tx.baseRevision)+1n,values=[];
     for(let i=start;i<start+count;i++){const line=JSON.stringify({id:`${f}-${i}`,at:'2026-11-01',price:'1.00',note:i%1000===0?'needle hit '+i:'common row '+i});fileBytes+=Buffer.byteLength(line)+1;values.push([path,`${f}-${i}`,`2026-11-01 ${String(f).padStart(3,'0')} ${String(i).padStart(8,'0')}`,at,null,'all',Buffer.from(line)])}
     await tx.executeMany('INSERT INTO public_rows(file,row_key,sort_key,rev_from,rev_to,audience,line) VALUES(?,?,?,?,?,?,?)',values,{maxRows:values.length,maxInputBytes:16*1024*1024});
     await tx.execute({sql:'INSERT INTO public_fts(rowid,line) SELECT rowid,line FROM public_rows WHERE file=? AND rev_from=?',parameters:[path,at]});
    });
   }
   bytes+=fileBytes;
   await run.tx(`b5-bench-meta-${f}`,async tx=>tx.execute({sql:'INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,NULL,?,?,?,?,?,?)',parameters:[path,dir,BigInt(tx.baseRevision)+1n,table.path,perFile,fileBytes,'["all"]',name,'jsonl']}));
  }
  await run.openMoment('m');
  const percentile=(sorted,p)=>sorted[Math.min(sorted.length-1,Math.ceil(p*sorted.length)-1)];
  let cold;
  const hot=[];
  const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-bench-hot'));
  try{
   await session.value.withView(async view=>{
    await view.grep({root:'/public',pattern:'needle',mode:'literal',page});
    for(let i=0;i<10;i++){const t=performance.now();await view.grep({root:'/public',pattern:'needle',mode:'literal',page});hot.push(performance.now()-t)}
   });
  }finally{await session.release()}
  const coldSession=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-bench-cold'));
  try{await coldSession.value.withView(async view=>{const t=performance.now();await view.grep({root:'/public',pattern:'needle',mode:'literal',page});cold=performance.now()-t})}finally{await coldSession.release()}
  const sorted=[...hot].sort((a,b)=>a-b),p50=percentile(sorted,0.5),p95=percentile(sorted,0.95);
  assert.ok(p50<100,`literal hot p50 ${p50.toFixed(2)} ms`);
  assert.ok(cold<1000,`literal cold ${cold.toFixed(2)} ms`);

  const regex='[nc][ea][oe]d';
  let regexMs=0;
  const regexSession=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-bench-regex'));
  try{await regexSession.value.withView(async view=>{await view.grep({root:'/public',pattern:regex,mode:'regex',page});const t=performance.now();await view.grep({root:'/public',pattern:regex,mode:'regex',page});regexMs=performance.now()-t})}finally{await regexSession.release()}
  const disk=await run.read(async view=>{
   const found=await run.ctx.public.mount.find('/public','*.jsonl',run.scope('a','m'),view,{maxItems:2000,maxResponseBytes:64*1024*1024});
   return found.items.map(node=>run.ctx.public.cache.pathOf(String(node.provenance?.etag)));
  });
  const baseline=new NativeSearch(regex,'regex',{maxInlineFileBytes:16777216,maxSearchBytes:67108864,maxSearchOutputBytes:16777216,maxSearchMs:30000},false,disk);
  const controller=new AbortController();
  const start=performance.now();
  await baseline.runFiles(controller.signal,()=>true);
  const baselineMs=performance.now()-start;
  const ratio=regexMs/baselineMs;
  console.log(JSON.stringify({case:'B5-grep-bench',bytes,cold:Number(cold.toFixed(2)),p50:Number(p50.toFixed(2)),p95:Number(p95.toFixed(2)),regexMs:Number(regexMs.toFixed(2)),rgMs:Number(baselineMs.toFixed(2)),ratio:Number(ratio.toFixed(2))}));
  // 7.49 MB 小数据集只作记录；阈值断言在 b5-grep-bench-large。
 }finally{await run.close()}
});
