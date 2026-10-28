import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,readdir,stat,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
const exec=promisify(execFile),[consumerPath,outputPath,profile='profiles/rule-sqlite.json',lock='module-lock.json']=process.argv.slice(2);
if(!consumerPath||!outputPath)throw Error('Usage: node pr12-boundaries.mjs <installed-consumer> <new-evidence-directory> [profile] [lock]');
const consumer=resolve(consumerPath),output=resolve(outputPath);await mkdir(output,{recursive:false});
const directory=join(output,'existing-world'),lineage=join(output,'lineages','existing-world');await mkdir(directory);
const args=['run-profile.mjs','--profile',profile,'--directory',directory,'--run-id','boundary','--lock',lock,'--lineage-directory',lineage];
const failures=[];
for(const [extra,code]of [[[],'FRESH_RUN_EXISTS'],[['--reopen'],'REOPEN_AUTHORITY_UNAVAILABLE']]){
 await assert.rejects(()=>exec(process.execPath,[...args,...extra],{cwd:consumer,maxBuffer:1048576,timeout:180000}),error=>{failures.push({mode:extra.length?'reopen':'fresh',exitCode:error.code,stderr:error.stderr});return error.code===1&&error.stderr.includes(code)});
 assert.deepEqual(await readdir(directory),[]);await assert.rejects(()=>stat(lineage),{code:'ENOENT'});
}
const report={profile,lock,failures,worldUnchanged:true,missingAuthorityNotCreated:true,scope:'Real ProfileLoader compile followed by pre-DB admission refusal; no business execution'};
await writeFile(join(output,'boundaries.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
