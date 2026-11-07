import {test} from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {readdir} from 'node:fs/promises';
import {setup} from './b4-support.mjs';
import {NativeSearch} from '../.build/core/dist/exploration/search.js';

const table={path:'/public/market/shards',title:'分片',description:'grep 基准分片',schema:{type:'object',properties:{id:{type:'string'},shard:{type:'string'},note:{type:'string'}}},key:['id'],sort:[{field:'id'}],shard:{by:'shard',format:'value'}};
const files=400,perFile=1000,chunk=5000,pad='x'.repeat(1050);
const page={maxItems:4000,maxResponseBytes:32*1024*1024};
const limits={maxInlineFileBytes:16777216,maxSearchBytes:536870912,maxSearchOutputBytes:67108864,maxSearchMs:30000};

test('400MB级grep：字面量冷/热与正则rg基线',{skip:!process.env.PR05_CROSSFS},async()=>{
 const run=await setup({tables:[table],exploration:limits});try{
  const seedStarted=performance.now();let bytes=0;
  for(let start=0;start<files*perFile;start+=chunk){
   const rows=[];
   for(let index=start;index<Math.min(files*perFile,start+chunk);index++){
    const shard=Math.floor(index/perFile),i=index%perFile;
    const note=shard<4&&i===0?'needle hit '+shard:pad+' '+i;
    const line=JSON.stringify({id:`${shard}-${i}`,shard:`g${String(shard).padStart(3,'0')}`,note});
    bytes+=Buffer.byteLength(line)+1;
    rows.push({id:`${shard}-${i}`,shard:`g${String(shard).padStart(3,'0')}`,note});
   }
   await run.tx(`b5-large-seed-${start}`,tx=>run.ctx.public.writer(tx).upsert(table.path,rows));
  }
  const seedMs=performance.now()-seedStarted;
  assert.ok(bytes>=400*1024*1024,`数据集 ${bytes} 不足 400MB`);
  console.log(JSON.stringify({case:'B5-grep-bench-large-seed',bytes,seedMs:Number(seedMs.toFixed(2))}));
  await run.openMoment('m');
  assert.equal(run.ctx.public.diagnostics().searchStale,0,'正常写入后 state 必须等于新 revision');
  const percentile=(sorted,p)=>sorted[Math.min(sorted.length-1,Math.ceil(p*sorted.length)-1)];
  const sample=async body=>{await body();const times=[];for(let i=0;i<6;i++){const t=performance.now();await body();times.push(performance.now()-t)}return times};
  const median=times=>{const sorted=[...times].sort((a,b)=>a-b);return sorted[Math.floor((sorted.length-1)/2)]};
  const view=await run.ctx.database.snapshot({maxAgeMs:300000});
  let cold,literalTimes;
  try{
   let t=performance.now();await run.ctx.public.mount.search({root:'/public',pattern:'needle',mode:'literal'},run.scope('a','m'),view,new AbortController().signal,()=>true);cold=performance.now()-t;
   literalTimes=await sample(()=>run.ctx.public.mount.search({root:'/public',pattern:'needle',mode:'literal'},run.scope('a','m'),view,new AbortController().signal,()=>true));
  }finally{await view.close()}
  const sorted=[...literalTimes].sort((a,b)=>a-b),p50=percentile(sorted,0.5),p95=percentile(sorted,0.95);
  assert.ok(cold<1000,`literal cold ${cold.toFixed(2)} ms`);
  assert.ok(p50<100,`literal hot p50 ${p50.toFixed(2)} ms`);

  const regex='[nc][ea][oe]d';
  const options=limits;
  const regexView=await run.ctx.database.snapshot({maxAgeMs:300000});
  let productMatches=0;await run.ctx.public.mount.search({root:'/public',pattern:regex,mode:'regex'},run.scope('a','m'),regexView,new AbortController().signal,()=>{productMatches++;return true});
  let regexTimes;
  try{regexTimes=await sample(()=>run.ctx.public.mount.search({root:'/public',pattern:regex,mode:'regex'},run.scope('a','m'),regexView,new AbortController().signal,()=>true))}finally{await regexView.close()}
  const cacheDir=join(run.directory,'public-cache'),disk=[];
  for(const dir of await readdir(cacheDir))for(const name of await readdir(join(cacheDir,dir)))disk.push(join(cacheDir,dir,name));
  assert.equal(disk.length,files,'正则预热后每个文件都应有缓存');
  let baselineMatches=0;
  {const baseline=new NativeSearch(regex,'regex',options,false,disk);await baseline.runFiles(new AbortController().signal,()=>{baselineMatches++;return true});}
  assert.equal(productMatches,baselineMatches,'产品与基线匹配数必须一致');
  assert.ok(productMatches>0,'正则基线不得空扫');
  const rgTimes=await sample(async()=>{
   const baseline=new NativeSearch(regex,'regex',options,false,disk);
   await baseline.runFiles(new AbortController().signal,()=>true);
  });
  const regexP50=median(regexTimes),rgP50=median(rgTimes),ratio=regexP50/rgP50;
  console.log(JSON.stringify({case:'B5-grep-bench-large',bytes,matches:productMatches,seedMs:Number(seedMs.toFixed(2)),cold:Number(cold.toFixed(2)),p50:Number(p50.toFixed(2)),p95:Number(p95.toFixed(2)),regexP50:Number(regexP50.toFixed(2)),rgP50:Number(rgP50.toFixed(2)),overheadMs:Number((regexP50-rgP50).toFixed(2)),ratio:Number(ratio.toFixed(2))}));
  assert.ok(ratio<=1.3,`regex/rg ${ratio.toFixed(2)} > 1.3`);
 }catch(error){console.error(JSON.stringify(await run.ctx.database.nativeDiagnostics()));throw error}finally{await run.close()}
});
