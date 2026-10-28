import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {platform,release,arch,cpus} from 'node:os';
import {performance} from 'node:perf_hooks';
import {loadavg,availableParallelism} from 'node:os';
const exec=promisify(execFile);
const [consumerPath,outputPath]=process.argv.slice(2);
if(!consumerPath||!outputPath)throw Error('Usage: node pr12-scale.mjs <installed-consumer> <new-evidence-directory>');
const consumer=resolve(consumerPath),output=resolve(outputPath);await mkdir(output,{recursive:false});
const measurements=[];
for(const [world,steps,bodyBytes] of [[100,1,128],[1000,1,128],[5000,1,128],[100,100,128],[100,1,8192]]){
 const id=`w${world}-h${steps}-b${bodyBytes}`,directory=join(output,id),patches=join(output,id+'.json');
 const initialActors=Array.from({length:world},(_,i)=>({id:i===0?'alice':'actor-'+i,attributes:{body:'x'.repeat(bodyBytes)}}));
 const proposals=Array.from({length:steps},(_,i)=>({id:'p'+i,planKind:'staged/1',payload:{phases:[{id:'act',activations:[{id:'a'+i,actorId:'alice',momentId:'m'+i,source:'agenda',driver:'rule',input:null}]}],nextCursor:String(i+1)}}));
 await writeFile(patches,JSON.stringify([{id:'plain-init',config:{initialActors}},{id:'proposal-sequence',config:{proposals}}]));
 const args=['run-profile.mjs','--profile','profiles/rule-sqlite.json','--lock','module-lock.json','--directory',directory,'--run-id',id,'--patches',patches];
 const result={id,world,steps,bodyBytes};
 try{
  for(const reopen of [false,true]){
   const processStarted=performance.now();
   const {stdout,stderr}=await exec(process.execPath,[...args,...reopen?['--reopen']:[]],{cwd:consumer,maxBuffer:16*1024*1024,timeout:600000});
   const data=JSON.parse(stdout);
   result[reopen?'reopen':'create']={processWallMs:performance.now()-processStarted,actorCount:data.actorCount,metrics:data.metrics,history:data.history,point:data.cut,status:data.status.state,close:data.close.status,stderr};
  }
  result.status='measured';
 }catch(error){result.status='failed';result.error={message:error.message,stdout:error.stdout,stderr:error.stderr};}
 measurements.push(result);
 await writeFile(join(output,'measurements.json'),JSON.stringify({platform:{platform:platform(),release:release(),arch:arch(),node:process.version,cpu:cpus()[0]?.model,loadavg:loadavg(),availableParallelism:availableParallelism()},units:{wallTime:'milliseconds',memoryUsage:'bytes',maxRSS:'KiB on Linux',resourceUsageCPUTime:'microseconds',fsReadWrite:'OS input/output block operation counts, not bytes'},unavailable:['pure compression CPU time','isolated stream read/write time','storage device IO latency'],timingSemantics:'History timings are cumulative monotonic wall time, nested and overlapping; do not sum phases. Native phases include worker startup and physical exit; codec pipelines include stream IO.',measurements},null,2));
}
console.log(JSON.stringify(measurements.map(({id,status,create,reopen,error})=>({id,status,create,reopen,error}))));
if(measurements.some(x=>x.status==='failed'))process.exitCode=1;
