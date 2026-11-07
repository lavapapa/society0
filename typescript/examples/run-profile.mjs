#!/usr/bin/env node
import {readFile,mkdir,stat} from 'node:fs/promises';
import {resolve,join,dirname,basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {applyEntryPatches} from '@society0/include';
import {performance} from 'node:perf_hooks';

export async function runProfile(options) {
  validateMode(options);
  const started=performance.now(),{createHost}=await import('@society0/host');
  const run=await readRun(options);run.started=started;run.metrics={};
  run.host=await createHost({baseURL:import.meta.url,boot:hostBoot(run)});
  try {
    await compileRun(run);
    if(options.inspect)return {mode:'inspect',plan:run.host.ctx.profileLoader.dump(run.plan),modelRequests:0};
    resolveRunDefinition(run);authorizeRun(run);
    await admitStorage(run);await options.configureHost?.(run.host.ctx);
    await collectMigrations(run);run.metrics.preparationMs=performance.now()-started;
    await openRun(run);
    return run.report=await consumeRun(run);
  } finally {await closeRun(run);}
}
function validateMode(options){
  if(options.observe&&!options.reopen)throw Error('OBSERVE_REQUIRES_REOPEN: use --reopen --observe with an existing run directory');
}
function literalEntries(entries){return entries.flatMap(entry=>entry.group?literalEntries(entry.config??[]):[entry]);}
function entryConfig(entries,id){return entries.find(entry=>entry.id===id)?.config??{};}
function activeModule(entries,name){return entries.some(entry=>!entry.disabled&&entry.name===name);}
async function readRun(options){
  const profileURL=pathToFileURL(resolve(options.profile)),profile=JSON.parse(await readFile(profileURL,'utf8'));
  if(options.lock)profile.lockfile=pathToFileURL(resolve(options.lock)).href;
  const directory=resolve(options.directory),runId=options.runId??'example';
  const lineageDirectory=resolve(options.lineageDirectory??join(dirname(directory),'lineages',basename(directory))),lineage=options.lineage??runId;
  const entries=literalEntries(applyEntryPatches([],profile.patches,message=>{throw Error('PROFILE_PATCH: '+message)}));
  return {options,profileURL,profile,directory,runId,lineageDirectory,lineage,entries,event:activeModule(entries,'@society0/core/runtime-events'),social:activeModule(entries,'@society0/social/social')};
}
function hostBoot(run){
  const names=['resources','tasks','composition-strategies','slot-strategies','module-resolver','config-resolvers','profile-loader','inspector','run-directory'];
  const seed=names.map(name=>({specifier:'@society0/host/'+name}));
  if(run.entries.some(entry=>['@society0/core/model-pi','@society0/core/model-codex-subscription'].includes(entry.name)))seed.push({specifier:'@society0/host/slots'},{specifier:'@society0/core/model-credentials',config:{stores:[]}});
  if(run.options.branchDirectory)seed.push({specifier:'@society0/host/commands'},{specifier:'@society0/host/run-branch',config:{directory:resolve(run.options.branchDirectory)}});
  return seed;
}
async function compileRun(run){
  const {directory,runId,lineageDirectory,lineage,entries}=run;
  const overrides=[{id:'sqlite',config:{...entryConfig(entries,'sqlite'),directory,runId}},{id:'artifacts-local',config:{...entryConfig(entries,'artifacts-local'),directory:join(directory,'artifacts')}},{id:'sqlite-history',config:{...entryConfig(entries,'sqlite-history'),directory,runId}},{id:'attempts',config:{...entryConfig(entries,'attempts'),directory:lineageDirectory,lineage}}];
  if(run.options.patches)overrides.push(...JSON.parse(await readFile(resolve(run.options.patches),'utf8')));
  const started=performance.now();run.plan=await run.host.ctx.profileLoader.compile(run.profile,{baseURL:run.profileURL.href,cliPatches:overrides});run.metrics.profileCompileMs=performance.now()-started;
}
function resolveRunDefinition(run){
  run.modelEnabled=activeModule(run.plan.flat,'@society0/core/driver-llm');
  run.databaseEntry=run.plan.flat.find(entry=>entry.id==='sqlite');run.historyEntry=run.plan.flat.find(entry=>entry.id==='sqlite-history');
  run.source=run.databaseEntry.binding.identity.source;run.directory=resolve(run.databaseEntry.config.directory);
  run.authorityDirectory=resolve(run.plan.flat.find(entry=>entry.id==='attempts').config.directory);
}
function authorizeRun(run){
  if(run.options.offline||run.options.observe)return;
  if(activeModule(run.plan.flat,'@society0/core/model-codex-subscription'))throw Error('CODEX_NOT_AUTHORIZED: use --inspect for offline configuration; account login requires separate authorization');
  if(run.modelEnabled&&!run.options.allowModelRequests)throw Error('MODEL_NOT_AUTHORIZED: use --inspect; live execution must be coordinated by the authorized model owner');
}
async function admitStorage(run){
  if(run.options.reopen)return verifyExistingStorage(run);
  await reserveFreshStorage(run);
}
async function verifyExistingStorage({directory,authorityDirectory}){
  try{await stat(join(authorityDirectory,'attempts.sqlite'));await stat(join(directory,'world.sqlite'));}
  catch(error){throw Error('REOPEN_AUTHORITY_UNAVAILABLE: preserve '+directory+' and '+authorityDirectory+'; existing run storage and lineage authority are required; no authority was initialized',{cause:error});}
}
async function reserveFreshStorage({directory,authorityDirectory}){
  await mkdir(dirname(directory),{recursive:true});
  try{await mkdir(directory);}catch(error){throw Error('FRESH_RUN_EXISTS: '+directory+'; select a new directory or explicitly use --reopen; preserve any missing authority as unknown',{cause:error});}
  await mkdir(dirname(authorityDirectory),{recursive:true});
  try{await mkdir(authorityDirectory);}catch(error){throw Error('FRESH_LINEAGE_EXISTS: '+authorityDirectory+'; select a new lineage directory; existing authority will not be initialized',{cause:error});}
}
async function collectMigrations(run){
  const [{artifactsMigration},{actorsMigration},{resultsMigration},{threadMigration},{actionsMigration}]=await Promise.all(['artifacts-local','actors','results','thread','actions'].map(name=>import('@society0/core/'+name)));
  const {source}=run;run.migrations=[artifactsMigration,actorsMigration,resultsMigration(source),threadMigration(source),actionsMigration(source)];
  if(activeModule(run.plan.flat,'@society0/core/perception'))run.migrations.push((await import('@society0/core/perception')).perceptionMigration(source));
  if(activeModule(run.plan.flat,'@society0/core/moment-views'))run.migrations.push((await import('@society0/core/moment-views')).momentViewsMigration(source));
  if(activeModule(run.plan.flat,'@society0/core/public'))run.migrations.push((await import('@society0/core/public')).publicMigration(source),(await import('@society0/core/public')).publicIndexMigration(source));
  await collectWorkspaceMigration(run);
  if(run.modelEnabled)run.migrations.push((await import('@society0/core/actor-tools')).actorToolsMigration(source));
  if(run.event)run.migrations.push((await import('@society0/core/runtime-events')).eventsMigration);
  if(run.social)run.migrations.push((await import('@society0/social/social-repository')).socialMigration,(await import('@society0/social/measurement-repository')).measurementMigration);
}
async function collectWorkspaceMigration(run){
  const entry=run.plan.flat.find(entry=>!entry.disabled&&entry.name==='@society0/extras/workspace');
  if(!entry)return;
  run.workspace=await import('@society0/extras/workspace');run.migrations.push(run.workspace.workspaceMigration(entry.binding.identity.source));
}
async function openRun(run){
  const started=performance.now();
  run.handle=await run.host.ctx.runDirectory.create({runId:run.runId,generation:run.plan,mount:{runEntryId:'run',bootstrapEntryId:'bootstrap'},prepare:(ctx,phase)=>prepareRun(run,ctx,phase)});
  run.metrics[run.options.reopen?'recoverMs':'createMs']=performance.now()-started;
  run.lease=run.host.ctx.runDirectory.acquire(run.runId,run.options.observe?'read':'write');
  run.consumerFiber=await run.lease.ctx.plugin({name:'public.consume',inject:['runs','observation','history','database','attempts'],apply(ctx){run.ctx=ctx}});await run.consumerFiber.await();run.nativeScopes=nativeScopes(run);
}
async function prepareRun(run,bootstrapCtx,phase){
  run.mountedTree=phase.tree;await registerWorkspaceSeed(run,bootstrapCtx);
  let ctx;const consumer=await phase.run.plugin({name:'public.prepare',inject:['attempts','sqliteBootstrap','history','database'],apply(value){ctx=value}});await consumer.await();
  if(run.options.reopen)await ctx.attempts.verifyAuthority();else await ctx.attempts.initialize();
  const root=await prepareRoot(run,ctx),status=await ctx.history.status();
  return {root,metadata:ctx.database.metadata,database:ctx.database,state:status.state};
}
async function registerWorkspaceSeed(run,bootstrapCtx){
  if(!run.workspace||run.options.reopen)return;
  const consumer=await bootstrapCtx.plugin({name:'public.workspace-seed',inject:['initializers'],apply(ctx){ctx.initializers.register(run.workspace.workspaceInitializer({after:[run.social?'social.seed':'plain.seed']}))}});await consumer.await();
}
async function prepareRoot(run,ctx){
  if(run.options.reopen)return run.options.observe?ctx.history.current():ctx.history.activate();
  return ctx.sqliteBootstrap.create({operationId:'create:'+run.runId,runId:run.runId,directory:run.directory,input:{},migrations:run.migrations,initialFrontier:initialFrontier(run.event),initialControl:{generation:run.databaseEntry.config.generation,controlSequence:run.historyEntry.config.controlSequence,planId:run.profileURL.href},requiredInitializerIds:[run.social?'social.seed':'plain.seed',...run.workspace?['workspace.seed']:[]]});
}
function initialFrontier(event){return {codec:event?'events/1':'staged/1',value:'0',logicalTime:event?{time:'0',offset:'0'}:'0',completedCount:'0',pendingAcknowledgements:[]};}
async function feedExampleEvent(run){
  if(!run.event||run.options.offline||run.options.observe)return;
  const consumer=await run.mountedTree.entries.get('generation').ctx.plugin({name:'public.events',inject:['proposalSource'],async apply(ctx){await ctx.proposalSource.push({id:'event-1',offset:'1',time:'1',payload:{handler:'plain.update',actorId:'alice',attributes:{name:'Alice',message:'public SDK event'}}});ctx.proposalSource.end()}});await consumer.await();await consumer.dispose();
}
async function advanceRun(run){
  if(run.options.observe)return {status:'observation-only',advanced:false};
  if(run.options.offline)return {status:'offline-mounted',advanced:false};
  return run.ctx.runs.run(new AbortController().signal);
}
function requireCompleted(run,result){
  if(run.options.offline||run.options.observe)return;
  if(result.status!=='completed')throw Error('RUN_'+result.status+': '+JSON.stringify({runId:run.runId,directory:run.directory,...result})+'; preserve the run directory and probe the reported complete point before retrying');
}
async function observeRun(run){
  const started=performance.now(),ctx=run.ctx;
  const actorCount=(await ctx.observation.query({sql:'SELECT COUNT(*) AS count FROM s0_actors'})).rows[0].count;
  const observation=await ctx.observation.query({sql:'SELECT id,attributes FROM s0_actors ORDER BY id'});
  const receipts=await ctx.observation.query({sql:'SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id'});
  const socialObservation=await observeSocial(run);run.metrics.queryMs=performance.now()-started;
  const status=await ctx.observation.status(),cut=await ctx.history.current();return {actorCount,observation,receipts,socialObservation,status,cut};
}
async function observeSocial(run){
  if(!run.social)return;
  return {posts:await run.ctx.observation.query({sql:'SELECT id,author_id,content FROM social_posts ORDER BY ordinal'}),deliveries:await run.ctx.observation.query({sql:'SELECT event_id,post,recipient,sender FROM social_deliveries ORDER BY ordinal'})};
}
function nativeScopes(run){return {bootstrapDisposed:!run.mountedTree.entries.has('bootstrap'),generationMounted:run.mountedTree.entries.has('generation'),leaseUsesRunContext:run.lease.ctx===run.mountedTree.entries.get('run').ctx};}
function modelRequests(run,branch){
  if(run.options.afterRun)return branch?.modelRequests??null;
  return run.modelEnabled&&!run.options.offline&&!run.options.observe?null:0;
}
async function consumeRun(run){
  await feedExampleEvent(run);const before=await run.ctx.history.current(),started=performance.now();
  const result=await advanceRun(run);run.metrics.advanceAndSaveMs=performance.now()-started;requireCompleted(run,result);
  const observed=await observeRun(run);run.metrics.memory=process.memoryUsage();run.metrics.resourceUsage=process.resourceUsage();run.metrics.totalMs=performance.now()-run.started;
  const {host,plan,profileURL,directory,runId}=run;
  const branch=await run.options.afterRun?.({host,ctx:run.ctx,cut:observed.cut,plan,tree:run.mountedTree,profileURL,directory,runId});
  return {branch,profile:profileURL.href,runId,directory,reopened:!!run.options.reopen,root:run.handle.root,before,result,nativeScopes:run.nativeScopes,lineage:run.ctx.attempts.authority(),configuration:host.ctx.profileLoader.dump(plan),source:run.source,...observed,history:run.ctx.history.diagnostics(),metrics:run.metrics,modelRequests:modelRequests(run,branch)};
}
async function closeRun(run){
  const started=performance.now();await run.consumerFiber?.dispose();await run.lease?.release();await run.handle?.close();const closed=await run.host.close();
  if(run.report){run.report.close=closed;run.metrics.closeMs=performance.now()-started;run.metrics.totalMs=performance.now()-run.started;}
}

async function main(){
 const {values}=parseArgs({options:{profile:{type:'string'},directory:{type:'string'},'lineage-directory':{type:'string'},lineage:{type:'string'},lock:{type:'string'},patches:{type:'string'},'run-id':{type:'string'},reopen:{type:'boolean'},offline:{type:'boolean'},observe:{type:'boolean'},inspect:{type:'boolean'},help:{type:'boolean'}}});
 if(values.help){console.log('Usage: node run-profile.mjs --profile <profile.json> --directory <run-directory> --lock <module-lock.json> [--run-id <id>] [--lineage-directory <path>] [--lineage <id>] [--patches <patch-array.json>] [--reopen] [--inspect] [--offline] [--observe]\nFresh requires unused world and lineage directories. Reopen requires existing world and authority; fork must retain the source lineage identity and directory. Create or reopen a real SQLite run through ProfileLoader and RunDirectory. --inspect validates and reports effective config without opening a run or requesting a model. --reopen activates existing history and skips seed. --offline mounts native providers, opens the root and observes it without advancing or reading model credentials. --reopen --observe reads the existing source point and live state without advancing or activating a dirty run. Output: one JSON result; failures: stderr and exit 1.');return;}
 if(!values.profile||!values.directory)throw Error('PROFILE_ARGUMENTS: provide --profile and --directory; use --help');
 console.log(JSON.stringify(await runProfile({...values,runId:values['run-id'],lineageDirectory:values['lineage-directory']})));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)await main().catch(error=>{console.error(error);process.exitCode=1});
