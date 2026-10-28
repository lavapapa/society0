import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
const exec=promisify(execFile),[consumerPath,outputPath]=process.argv.slice(2);
if(!consumerPath||!outputPath)throw Error('Usage: node pr12-offline-profiles.mjs <installed-consumer> <new-evidence-directory>');
const consumer=resolve(consumerPath),output=resolve(outputPath);await mkdir(output,{recursive:false});
for(const name of ['social-sqlite','social-codex-sqlite']){
 const args=['run-profile.mjs','--profile','profiles/'+name+'.json','--lock','module-lock.json','--directory',join(output,name),'--run-id',name];
 const {stdout,stderr}=await exec(process.execPath,[...args,'--offline'],{cwd:consumer,maxBuffer:8*1024*1024,timeout:120000});
 assert.equal(stderr,'');const report=JSON.parse(stdout);
 assert.deepEqual(report.nativeScopes,{bootstrapDisposed:true,generationMounted:true,leaseUsesRunContext:true});
 assert.equal(report.result.status,'offline-mounted');assert.equal(report.result.advanced,false);
 assert.equal(report.cut.frontier.completedCount,'0');assert.equal(report.modelRequests,0);assert.equal(report.status.state,'clean');
 assert.equal(report.close.status,'closed');assert.equal(report.close.drained,true);
 await writeFile(join(output,name+'.json'),JSON.stringify(report,null,2));
 const reopened=JSON.parse((await exec(process.execPath,[...args,'--offline','--reopen'],{cwd:consumer,maxBuffer:8*1024*1024,timeout:120000})).stdout);
 assert.equal(reopened.cut.id,report.cut.id);assert.equal(reopened.modelRequests,0);assert.equal(reopened.close.status,'closed');
 await writeFile(join(output,name+'-reopen.json'),JSON.stringify(reopened,null,2));
 await assert.rejects(()=>exec(process.execPath,args,{cwd:consumer,maxBuffer:1048576,timeout:120000}),error=>error.code===1&&/MODEL_NOT_AUTHORIZED|CODEX_NOT_AUTHORIZED/.test(error.stderr));
}
console.log(JSON.stringify({offlineProfiles:'native-mounted-root-observed-closed',modelRequests:0,liveSubscription:'not-authorized/unverified'}));
