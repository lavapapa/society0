import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,table} from './b4-support.mjs';
import {NativeSearch} from '../.build/core/dist/exploration/search.js';

const dir='/public/market/prices',files=4,perFile=105000,chunk=2000;
const page={maxItems:4000,maxResponseBytes:32*1024*1024};
const pad='x'.repeat(950);

test('400MB级grep：字面量冷/热与正则rg基线',{skip:!process.env.PR05_CROSSFS},async()=>{
 const run=await setup();try{
  const seedStarted=performance.now();
  let bytes=0;
  for(let f=0;f<files;f++){
   const name=`f${String(f).padStart(5,'0')}.jsonl`,path=`${dir}/${name}`;
   let fileBytes=0;
   for(let start=0;start<perFile;start+=chunk){
    const count=Math.min(chunk,perFile-start);
    await run.tx(`b5-large-seed-${f}-${start}`,async tx=>{
     const at=BigInt(tx.baseRevision)+1n,values=[];
     for(let i=start;i<start+count;i++){
      const note=f===0&&i%5000===0?'needle hit '+i:pad+' '+i;
      const line=JSON.stringify({id:`${f}-${i}`,at:'2026-11-01',price:'1.00',note});
      fileBytes+=Buffer.byteLength(line)+1;
      values.push([path,`${f}-${i}`,`2026-11-01 ${String(f).padStart(3,'0')} ${String(i).padStart(8,'0')}`,at,null,'all',Buffer.from(line)]);
     }
     await tx.executeMany('INSERT INTO public_rows(file,row_key,sort_key,rev_from,rev_to,audience,line) VALUES(?,?,?,?,?,?,?)',values,{maxRows:values.length,maxInputBytes:16*1024*1024});
     await tx.execute({sql:'INSERT INTO public_fts(rowid,line) SELECT rowid,line FROM public_rows WHERE file=? AND rev_from=?',parameters:[path,at]});
    });
   }
   bytes+=fileBytes;
   await run.tx(`b5-large-meta-${f}`,async tx=>tx.execute({sql:'INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,NULL,?,?,?,?,?,?)',parameters:[path,dir,BigInt(tx.baseRevision)+1n,table.path,perFile,fileBytes,'["all"]',name,'jsonl']}));
  }
  const seedMs=performance.now()-seedStarted;
  assert.ok(bytes>=400*1024*1024,`数据集 ${bytes} 不足 400MB`);
  console.log(JSON.stringify({case:'B5-grep-bench-large-seed',bytes,seedMs:Number(seedMs.toFixed(2))}));
  await run.openMoment('m');
  const percentile=(sorted,p)=>sorted[Math.min(sorted.length-1,Math.ceil(p*sorted.length)-1)];
  const view=await run.ctx.database.snapshot({maxAgeMs:300000});
  const mountGrep=(pattern,mode)=>run.ctx.public.mount.search({root:'/public',pattern,mode},run.scope('a','m'),view,new AbortController().signal,()=>true);
  let cold;const hot=[];
  try{
   let t=performance.now();await mountGrep('needle','literal');cold=performance.now()-t;
   for(let i=0;i<10;i++){t=performance.now();await mountGrep('needle','literal');hot.push(performance.now()-t)}
  }finally{await view.close()}
  const sorted=[...hot].sort((a,b)=>a-b),p50=percentile(sorted,0.5),p95=percentile(sorted,0.95);
  assert.ok(p50<100,`literal hot p50 ${p50.toFixed(2)} ms`);
  assert.ok(cold<1000,`literal cold ${cold.toFixed(2)} ms`);

  const regex='[nc][ea][oe]d';
  const sample=async body=>{await body();const times=[];for(let i=0;i<6;i++){const t=performance.now();await body();times.push(performance.now()-t)}return times};
  const median=times=>{const sorted=[...times].sort((a,b)=>a-b);return sorted[Math.floor((sorted.length-1)/2)]};
  const regexView=await run.ctx.database.snapshot({maxAgeMs:300000});
  let regexTimes;
  try{regexTimes=await sample(()=>run.ctx.public.mount.search({root:'/public',pattern:regex,mode:'regex'},run.scope('a','m'),regexView,new AbortController().signal,()=>true))}finally{await regexView.close()}
  const disk=await run.read(async view=>{
   const found=await run.ctx.public.mount.find('/public','*.jsonl',run.scope('a','m'),view,{maxItems:2000,maxResponseBytes:64*1024*1024});
   return found.items.map(node=>run.ctx.public.cache.pathOf(String(node.provenance?.etag)));
  });
  const baseline=new NativeSearch(regex,'regex',{maxInlineFileBytes:16777216,maxSearchBytes:67108864,maxSearchOutputBytes:16777216,maxSearchMs:30000},false,disk);
  const controller=new AbortController();
  const rgTimes=await sample(()=>baseline.runFiles(controller.signal,()=>true));
  const regexP50=median(regexTimes),rgP50=median(rgTimes),ratio=regexP50/rgP50;
  console.log(JSON.stringify({case:'B5-grep-bench-large',bytes,seedMs:Number(seedMs.toFixed(2)),cold:Number(cold.toFixed(2)),p50:Number(p50.toFixed(2)),p95:Number(p95.toFixed(2)),regexP50:Number(regexP50.toFixed(2)),rgP50:Number(rgP50.toFixed(2)),overheadMs:Number((regexP50-rgP50).toFixed(2)),ratio:Number(ratio.toFixed(2))}));
  assert.ok(ratio<=1.3,`regex/rg ${ratio.toFixed(2)} > 1.3`);
 }catch(error){console.error(JSON.stringify(await run.ctx.database.nativeDiagnostics()));throw error}finally{await run.close()}
});
