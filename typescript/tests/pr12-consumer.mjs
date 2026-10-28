import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,readFile,writeFile,rm,stat} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
const exec=promisify(execFile);
const [consumerPath,outputPath,...selected]=process.argv.slice(2);
if(!consumerPath||!outputPath)throw Error('Usage: node pr12-consumer.mjs <installed-consumer> <new-evidence-directory> [rule-sqlite event-sqlite]');
const consumer=resolve(consumerPath),output=resolve(outputPath);await mkdir(output,{recursive:false});
const results=[];
for(const name of selected.length?selected:['rule-sqlite','event-sqlite']){
 assert.ok(['rule-sqlite','event-sqlite'].includes(name),'zero-model lifecycle profiles only');
 const directory=join(output,name);
 const args=['run-profile.mjs','--profile','profiles/'+name+'.json','--directory',directory,'--run-id',name,'--lock','module-lock.json'];
 const invoke=async extra=>{
  const result=await exec(process.execPath,[...args,...extra],{cwd:consumer,maxBuffer:8*1024*1024,timeout:600000});
  assert.equal(result.stderr,'','public consumer emits no warnings or diagnostics on success');
  return JSON.parse(result.stdout);
 };
 const first=await invoke([]);await writeFile(join(output,name+'-create.json'),JSON.stringify(first,null,2));
 assert.deepEqual(first.nativeScopes,{bootstrapDisposed:true,generationMounted:true,leaseUsesRunContext:true});
 assert.equal(first.close.status,'closed');assert.equal(first.close.drained,true);assert.equal(first.result.status,'completed');assert.equal(first.cut.frontier.completedCount,'1');
 assert.equal(first.observation.rows.length,2);assert.equal(first.status.state,'clean');assert.equal(first.modelRequests,0);
 if(name==='event-sqlite'){assert.equal(JSON.parse(first.observation.rows.find(row=>row.id==='alice').attributes).message,'public SDK event');assert.equal(first.receipts.rows.filter(row=>row.action==='plain.update').length,1);}
 const bootstrap=await readFile(join(directory,'bootstrap.json'),'utf8');
 const reopened=await invoke(['--reopen']);await writeFile(join(output,name+'-reopen.json'),JSON.stringify(reopened,null,2));
 assert.equal(reopened.close.status,'closed');assert.equal(reopened.close.drained,true);assert.equal(reopened.before.id,first.cut.id);assert.deepEqual(reopened.observation.rows,first.observation.rows);
 assert.equal(await readFile(join(directory,'bootstrap.json'),'utf8'),bootstrap,'reopen must preserve seed authority');
 assert.equal(reopened.cut.frontier.completedCount,'1');assert.equal(reopened.status.state,'clean');
 const targetProfile=join(output,name+'-target-profile.json'),targetDirectory=join(output,name+'-fork'),targetRunId=name+'-fork';
 const changed=JSON.parse(await readFile(join(consumer,'profiles',name+'.json'),'utf8'));
 changed.patches.push({id:'checkpoint-policy',config:{every:2}});
 await writeFile(targetProfile,JSON.stringify(changed,null,2));
 const forked=JSON.parse((await exec(process.execPath,['run-profile-branch.mjs','--profile','profiles/'+name+'.json','--directory',directory,'--run-id',name,'--lock','module-lock.json','--target-profile',targetProfile,'--target-directory',targetDirectory,'--target-run-id',targetRunId],{cwd:consumer,maxBuffer:16*1024*1024,timeout:600000})).stdout);
 await writeFile(join(output,name+'-branch.json'),JSON.stringify(forked,null,2));
 assert.equal(forked.branch.closed,true);assert.equal(forked.close.status,'closed');assert.equal(forked.close.drained,true);
 assert.equal(forked.branch.targetPoint.runId,targetRunId);assert.deepEqual(forked.branch.observation.rows,first.observation.rows);assert.deepEqual(forked.branch.authority,first.lineage);
 assert.equal(forked.branch.sourcePoint.id,reopened.cut.id);assert.equal(forked.branch.targetPoint.frontier.completedCount,'1');assert.equal(forked.branch.status.state,'clean');
 const flat=entries=>entries.flatMap(entry=>[entry,...flat(entry.children??[])]);
 assert.equal(flat(forked.branch.plan.generationEvidence.plan.entries).find(entry=>entry.id==='checkpoint-policy').config.every,2);
 assert.equal(await readFile(join(directory,'bootstrap.json'),'utf8'),bootstrap);
 // 删除前核实本测试新建的直接子目录；authority必须位于该world目录之外。
 assert.equal(resolve(directory),resolve(output,name));assert.equal(dirname(resolve(directory)),output);assert.equal((await stat(directory)).isDirectory(),true);
 assert.ok(!resolve(first.lineage.directory).startsWith(resolve(directory)+'/'));
 await rm(directory,{recursive:true});
 const targetReopened=JSON.parse((await exec(process.execPath,['run-profile.mjs','--profile',join(targetDirectory,'profile.json'),'--directory',targetDirectory,'--run-id',targetRunId,'--lock','module-lock.json','--lineage-directory',first.lineage.directory,'--lineage',first.lineage.lineage,'--reopen','--observe'],{cwd:consumer,maxBuffer:16*1024*1024,timeout:600000})).stdout);
 await writeFile(join(output,name+'-target-reopen.json'),JSON.stringify(targetReopened,null,2));
 assert.equal(targetReopened.cut.id,forked.branch.targetPoint.id);assert.deepEqual(targetReopened.observation.rows,first.observation.rows);assert.equal(targetReopened.close.status,'closed');assert.equal(targetReopened.close.drained,true);
 results.push({profile:name,status:'create-save-close-reopen-fork-new-profile-delete-source-observe-close-passed',sourceWorldRemoved:directory,lineage:first.lineage,metrics:{create:first.metrics,reopen:reopened.metrics,fork:forked.branch.timings,targetReopen:targetReopened.metrics},history:forked.history});
}
await writeFile(join(output,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
