import assert from 'node:assert/strict';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import {writeFile} from 'node:fs/promises';
const consumer=resolve(process.argv[2]),evidence=resolve(process.argv[3]);const {runProfile}=await import(pathToFileURL(join(consumer,'run-profile.mjs')).href);const reports=[];
for(const name of ['rule-sqlite','event-sqlite','social-sqlite','social-codex-sqlite']){
 const simple=['rule-sqlite','event-sqlite'].includes(name),directory=join(evidence,simple?'final-lifecycles-01':'final-offline-01',name+(simple?'-fork':''));
 const result=await runProfile({profile:simple?join(directory,'profile.json'):join(consumer,'profiles',name+'.json'),directory,runId:name+(simple?'-fork':''),lock:join(consumer,'module-lock.json'),reopen:true,observe:true,offline:true,...simple?{lineageDirectory:join(evidence,'final-lifecycles-01/lineages',name),lineage:name}:{},async afterRun({host,tree,plan}){
  let details;const observer=await host.ctx.plugin({name:'public.runtime-inspect',inject:['inspector'],apply(ctx){details=ctx.inspector.treeDetails(tree)}});await observer.await();await observer.dispose();
  for(const actual of details){const declared=plan.flat.find(entry=>entry.id===actual.entryId);assert.ok(declared);assert.equal(actual.scope,declared.scope);assert.deepEqual(actual.config,declared.evidence);if(actual.scope==='bootstrap')assert.equal(actual.status,'unmounted');else if(!declared.disabled){assert.notEqual(actual.status,'unmounted');assert.equal(actual.missingServices.length,0,actual.entryId)}}
  return {profile:name,entries:details,modelRequests:0};
 }});assert.equal(result.close.drained,true);reports.push(result.branch);
}
await writeFile(join(evidence,'final-runtime-inspect.json'),JSON.stringify(reports,null,2),{flag:'wx'});console.log(JSON.stringify(reports.map(report=>({profile:report.profile,entries:report.entries.length,missingServices:report.entries.filter(entry=>entry.status!=='unmounted').flatMap(entry=>entry.missingServices),status:'matched'}))));
