import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {readFile,writeFile,mkdir} from 'node:fs/promises';import {resolve,join,basename} from 'node:path';import {pathToFileURL} from 'node:url';
const exec=promisify(execFile),[consumerPath,sourcePath,outputPath]=process.argv.slice(2),consumer=resolve(consumerPath),source=resolve(sourcePath),output=resolve(outputPath);await mkdir(output,{recursive:false});
const id='w100-h100-b128',profile=JSON.parse(await readFile(join(consumer,'profiles/rule-sqlite.json'),'utf8'));profile.lockfile=pathToFileURL(join(consumer,'module-lock.json')).href;profile.patches.push(...JSON.parse(await readFile(join(source,id+'.json'),'utf8')));
const profilePath=join(output,'source-profile.json');await writeFile(profilePath,JSON.stringify(profile),{flag:'wx'});
const measurements=[];
for(const [steps,pointId]of [[8,'cut-6eb85857-1457-499f-8f65-3075ecbbe9ff'],[32,'cut-f1718db2-8e59-4305-a4cf-41c729d5bb73'],[99,'cut-e84afdea-a68e-4d1c-9b88-a60cb4574ad9']]){
 const started=performance.now(),diagnostics=join(output,'segments-'+steps*2+'.ndjson');let value;
 try{
  const {stdout,stderr}=await exec(process.execPath,['run-profile-branch.mjs','--profile',profilePath,'--directory',join(source,id),'--run-id',id,'--lock','module-lock.json','--target-profile',join(consumer,'profiles/rule-sqlite.json'),'--target-directory',join(output,'target-'+steps),'--target-run-id',basename(output)+'-'+steps,'--point-id',pointId,'--diagnostics',diagnostics],{cwd:consumer,maxBuffer:16*1024*1024,timeout:steps===99?1800000:600000});
  const report=JSON.parse(stdout);assert.equal(report.branch.threadCount.integer,String(steps));assert.equal(report.branch.targetPoint.frontier.completedCount,String(steps));assert.equal(report.history.applyCount,steps*2);assert.equal(report.close.drained,true);
  value={status:'measured',steps,segments:steps*2,elapsedMs:performance.now()-started,diagnostics,history:report.history,timings:report.branch.timings,targetPoint:report.branch.targetPoint,stderr};
 }catch(error){value={status:'failed',steps,elapsedMs:performance.now()-started,diagnostics,error:{message:error.message,code:error.code,signal:error.signal,killed:error.killed,stdout:error.stdout,stderr:error.stderr}}}
 measurements.push(value);await writeFile(join(output,'measurements.json'),JSON.stringify(measurements,null,2));if(value.status==='failed')break;
}
console.log(JSON.stringify(measurements));if(measurements.some(value=>value.status==='failed'))process.exitCode=1;
