import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
const [consumerPath,outputPath]=process.argv.slice(2),consumer=resolve(consumerPath),output=resolve(outputPath);
await mkdir(output,{recursive:false});
const {runProfile}=await import(pathToFileURL(join(consumer,'run-profile.mjs')).href);
const profile=JSON.parse(await readFile(join(consumer,'profiles/social-sqlite.json'),'utf8'));
const walk=entries=>entries.flatMap(entry=>[entry,...entry.group?walk(entry.config):[]]);
const entries=walk(profile.patches.flatMap(patch=>patch.insert??[]));
const provider=entries.find(entry=>entry.name==='@society0/core/model-pi');provider.disabled=true;
const run=entries.find(entry=>entry.id==='run'),driver=entries.find(entry=>entry.name==='@society0/core/driver-llm'),prompts=entries.find(entry=>entry.name==='@society0/core/prompts');
const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
const message=(content,stopReason)=>({role:'assistant',api:'openai-completions',provider:'replay',model:'four-roots',content,stopReason,usage,timestamp:0});
run.config.push({id:'four-roots-replay',name:'@society0/core/model-replay',config:{name:'replay/v1',fixtures:{'four-roots':[
 message([{type:'toolCall',id:'shell-proof',name:'bash',arguments:{command:"ls /; printf 'workspace-proof' > /workspace/proof.txt; cat /workspace/proof.txt; cat /context/*"}}],'toolUse'),
 message([{type:'toolCall',id:'publish-proof',name:'publish',arguments:{content:'Explicit offline native replay proof'}}],'toolUse'),
 message([{type:'text',text:'Completed explicit replay'}],'stop')
]}}});
driver.config={...driver.config,provider:'replay/v1',model:'four-roots',maxRequests:3,maxToolCalls:2};
prompts.config={...prompts.config,contexts:[...(prompts.config.contexts??[]),{id:'four-roots-context',order:0,text:'frozen-context-proof'}]};
profile.lockfile=pathToFileURL(join(consumer,'module-lock.json')).href;
const profilePath=join(output,'explicit-replay-profile.json');await writeFile(profilePath,JSON.stringify(profile,null,2),{flag:'wx'});
const report=await runProfile({profile:profilePath,directory:join(output,'world'),runId:'four-roots-explicit-replay',lock:join(consumer,'module-lock.json'),allowModelRequests:true,async afterRun({ctx,tree,cut}){
 const tools=await ctx.observation.query({sql:'SELECT actor_id,moment_id,action,payload FROM actor_tool_receipts ORDER BY tool_call_id'});
 assert.equal(tools.rows.length,2);const shell=tools.rows.find(row=>row.action==='bash');assert.ok(shell);
 const stored=JSON.parse(shell.payload);assert.equal(stored.kind,'inline');assert.equal(stored.value.status,'completed');assert.equal(stored.value.value.exitCode,0);
 const stdout=stored.value.value.stdout;assert.equal(stdout.kind,'inline');for(const marker of ['world','results','workspace','context','workspace-proof','frozen-context-proof'])assert.ok(stdout.value.includes(marker),marker);
 const files=await ctx.observation.query({sql:"SELECT actor_id,path FROM workspace_nodes WHERE path='/workspace/proof.txt'"});assert.equal(files.rows.length,1);assert.equal(files.rows[0].actor_id,'alice');
 let generation;const owner=await tree.entries.get('generation').ctx.plugin({name:'public.context-proof',inject:['contextMount'],apply(value){generation=value}});await owner.await();
 const view=await ctx.database.snapshot({maxAgeMs:30000});let remaining;
 try{remaining=await generation.contextMount.list('/context',{actorId:shell.actor_id,momentId:shell.moment_id,logicalTime:cut.frontier.logicalTime,query:null},view,{maxItems:32,maxResponseBytes:65536});assert.equal(remaining.items.length,0)}finally{await view.close();await owner.dispose()}
 return {fixtureProvider:'@society0/core/model-replay',networkRequests:0,fixtureResponses:3,shell:stored.value.value,workspace:files,contextAfterActivation:remaining,toolReceipts:tools};
}});
assert.equal(report.close.drained,true);assert.equal(report.cut.frontier.completedCount,'1');assert.equal(report.socialObservation.posts.rows.length,1);
await writeFile(join(output,'report.json'),JSON.stringify(report,null,2),{flag:'wx'});
console.log(JSON.stringify({scenario:'explicit native replay; real PI agent loop, four roots and Social publish',cut:report.cut,proof:report.branch,close:report.close}));
