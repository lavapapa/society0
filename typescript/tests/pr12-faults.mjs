import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
const exec=promisify(execFile),[consumerPath,outputPath]=process.argv.slice(2);
if(!consumerPath||!outputPath)throw Error('Usage: node pr12-faults.mjs <installed-consumer> <new-evidence-directory>');
const consumer=resolve(consumerPath),output=resolve(outputPath);await mkdir(output,{recursive:false});
const patches=join(output,'bad-second-proposal.json'),directory=join(output,'source-point');
await writeFile(patches,JSON.stringify([{id:'proposal-sequence',config:{proposals:[
 {id:'first',planKind:'staged/1',payload:{phases:[{id:'act',activations:[{id:'a1',actorId:'alice',momentId:'m1',source:'agenda',driver:'rule',input:null}]}],nextCursor:'1'}},
 {id:'unsupported',planKind:'unsupported/1',payload:{nextCursor:'2'}}
]}}]));
const args=['run-profile.mjs','--profile','profiles/rule-sqlite.json','--directory',directory,'--run-id','source-point','--lock','module-lock.json','--patches',patches];
let failure;
try{await exec(process.execPath,args,{cwd:consumer,maxBuffer:1048576,timeout:120000});}
catch(error){failure={exitCode:error.code,stdout:error.stdout,stderr:error.stderr};}
assert.equal(failure?.exitCode,1);assert.match(failure.stderr,/RUN_failed/);assert.match(failure.stderr,/source-point/);
const read=async()=>JSON.parse((await exec(process.execPath,[...args,'--reopen','--observe'],{cwd:consumer,maxBuffer:8*1024*1024,timeout:120000})).stdout);
const observed=await read();assert.equal(observed.result.status,'observation-only');assert.equal(observed.cut.frontier.completedCount,'1');assert.equal(observed.close.status,'closed');
const again=await read();assert.equal(again.cut.id,observed.cut.id);assert.equal(again.cut.revision,observed.cut.revision);assert.deepEqual(again.observation.rows,observed.observation.rows);
const report={scenario:'unsupported second proposal after a real first complete point',failure,sourcePoint:observed.cut,observedState:observed.status.state,repeatedObservationLeavesSourceUnchanged:true,modelRequests:0,platformInterruption:'separate gate required'};
await writeFile(join(output,'source-point-failure.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
