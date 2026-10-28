import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {resolve,join,basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {platform,release,arch,cpus} from 'node:os';
import {performance} from 'node:perf_hooks';
import {loadavg,availableParallelism} from 'node:os';
const exec=promisify(execFile);
const [consumerPath,outputPath,sourcePath,mode]=process.argv.slice(2);
if(!consumerPath||!outputPath)throw Error('Usage: node pr12-scale.mjs <installed-consumer> <new-evidence-directory> [existing-measurements-directory] [historical]');
const consumer=resolve(consumerPath),output=resolve(outputPath);await mkdir(output,{recursive:false});
const prior=sourcePath?JSON.parse(await readFile(join(resolve(sourcePath),'measurements.json'),'utf8')):undefined;
if(mode==='historical'&&!prior)throw Error('Historical forks require prior measurements');
const measurements=[];
for(const [world,steps,bodyBytes] of [[100,1,128],[1000,1,128],[5000,1,128],[100,100,128],[100,1,8192]]){
 if(mode==='historical'&&steps===1)continue;
 const id=`w${world}-h${steps}-b${bodyBytes}`,directory=join(sourcePath?resolve(sourcePath):output,id),patches=join(output,id+'.json');
 const initialActors=Array.from({length:world},(_,i)=>({id:i===0?'alice':'actor-'+i,attributes:{body:'x'.repeat(bodyBytes)}}));
 const proposals=Array.from({length:steps},(_,i)=>({id:'p'+i,planKind:'staged/1',payload:{phases:[{id:'act',activations:[{id:'a'+i,actorId:'alice',momentId:'m'+i,source:'agenda',driver:'rule',input:null}]}],nextCursor:String(i+1)}}));
 const profile=JSON.parse(await readFile(join(consumer,'profiles/rule-sqlite.json'),'utf8'));profile.lockfile=pathToFileURL(join(consumer,'module-lock.json')).href;profile.patches.push({id:'plain-init',config:{initialActors}},{id:'proposal-sequence',config:{proposals}});
 await writeFile(patches,JSON.stringify(profile));
 const args=['run-profile.mjs','--profile',patches,'--lock','module-lock.json','--directory',directory,'--run-id',id];
 const selectedPoint=mode==='historical'?prior.measurements.find(item=>item.id===id).create.point.parentId:undefined;
 const caseStarted=performance.now();const result={id,world,steps,bodyBytes,selectedPoint};
 try{
  for(const reopen of sourcePath?[]:[false,true]){
   const processStarted=performance.now();
   const {stdout,stderr}=await exec(process.execPath,[...args,...reopen?['--reopen']:[]],{cwd:consumer,maxBuffer:16*1024*1024,timeout:600000});
   const data=JSON.parse(stdout);
   assert.equal(data.actorCount.integer,String(world));assert.equal(data.cut.frontier.completedCount,String(steps));assert.equal(data.close.drained,true);
   result[reopen?'reopen':'create']={processWallMs:performance.now()-processStarted,actorCount:data.actorCount,metrics:data.metrics,history:data.history,point:data.cut,status:data.status.state,close:data.close.status,stderr};
  }
  const forkStarted=performance.now();const fork=await exec(process.execPath,['run-profile-branch.mjs','--profile',patches,'--directory',directory,'--run-id',id,'--lock','module-lock.json','--target-profile',join(consumer,'profiles/rule-sqlite.json'),'--target-directory',join(output,id+'-fork'),'--target-run-id',id+'-'+basename(output),...(selectedPoint?['--point-id',selectedPoint]:[])],{cwd:consumer,maxBuffer:32*1024*1024,timeout:600000});
  const branched=JSON.parse(fork.stdout);assert.equal(branched.close.drained,true);assert.equal(branched.branch.closed,true);assert.equal(branched.branch.actorCount.integer,String(world));assert.equal(branched.branch.threadCount.integer,String(steps-(selectedPoint?1:0)));assert.equal(branched.branch.targetPoint.frontier.completedCount,String(steps-(selectedPoint?1:0)));if(prior){const original=prior.measurements.find(item=>item.id===id);assert.equal(branched.branch.sourcePoint.id,selectedPoint??original.create.point.id);assert.equal(branched.cut.id,original.create.point.id);result.source={directory,currentPoint:branched.cut,selectedPoint:branched.branch.sourcePoint};}
  result.fork={processWallMs:performance.now()-forkStarted,threadCount:branched.branch.threadCount,metrics:branched.branch.timings,history:branched.history,targetHistory:branched.branch.history,point:branched.branch.targetPoint};
  result.status='measured';
 }catch(error){result.status='failed';result.error={message:error.message,code:error.code,signal:error.signal,killed:error.killed,elapsedMs:performance.now()-caseStarted,stdout:error.stdout,stderr:error.stderr};}
 measurements.push(result);
 await writeFile(join(output,'measurements.json'),JSON.stringify({platform:{platform:platform(),release:release(),arch:arch(),node:process.version,cpu:cpus()[0]?.model,loadavg:loadavg(),availableParallelism:availableParallelism()},units:{wallTime:'milliseconds',memoryUsage:'bytes',maxRSS:'KiB on Linux',resourceUsageCPUTime:'microseconds',fsReadWrite:'OS input/output block operation counts, not bytes'},unavailable:['pure compression CPU time','isolated stream read/write time','storage device IO latency'],timingSemantics:'History timings are cumulative monotonic wall time, nested and overlapping; do not sum phases. Native phases include worker startup and physical exit; codec pipelines include stream IO.',measurements},null,2));
}
console.log(JSON.stringify(measurements.map(({id,status,create,reopen,fork,error})=>({id,status,create,reopen,fork,error}))));
if(measurements.some(x=>x.status==='failed'))process.exitCode=1;
