import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
const [consumerPath,inputPath,outputPath]=process.argv.slice(2);
const consumer=resolve(consumerPath),inputs=JSON.parse(await readFile(inputPath,'utf8'));
const {runProfile}=await import(pathToFileURL(join(consumer,'run-profile.mjs')).href);
const results=[];
for(const input of inputs){
 const run=await runProfile({profile:input.sourceProfile,directory:input.sourceDirectory,runId:input.sourceRunId,lock:join(consumer,'module-lock.json'),reopen:true,observe:true,async afterRun({ctx}){return {modelRequests:0,probe:await ctx.history.probeFork({operationId:input.operationId,targetDirectory:input.targetDirectory})}}});
 assert.equal(run.close.drained,true);if(input.journalStatus==='unknown')assert.equal(run.branch.probe.status,'prepared');
 results.push({...input,probe:run.branch.probe,sourcePoint:run.cut.id,closed:run.close.status});
}
await writeFile(outputPath,JSON.stringify(results,null,2),{flag:'wx'});console.log(JSON.stringify(results));
