#!/usr/bin/env node
import {readFile,mkdir,stat} from 'node:fs/promises';
import {resolve,join,dirname,basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {applyEntryPatches} from '@society0/include';
import {performance} from 'node:perf_hooks';

export async function runProfile(options) {
  if(options.observe&&!options.reopen)throw Error('OBSERVE_REQUIRES_REOPEN: use --reopen --observe with an existing run directory');
  const started=performance.now();
  const {createHost}=await import('@society0/host');
  const profileURL=pathToFileURL(resolve(options.profile));
  const profile=JSON.parse(await readFile(profileURL,'utf8'));
  if(options.lock) profile.lockfile=pathToFileURL(resolve(options.lock)).href;
  let directory=resolve(options.directory);const runId=options.runId??'example';
  const lineageDirectory=resolve(options.lineageDirectory??join(dirname(directory),'lineages',basename(directory))),lineage=options.lineage??runId;
  const flatten=entries=>entries.flatMap(e=>e.group?flatten(e.config??[]):[e]);
  const entries=flatten(applyEntryPatches([],profile.patches,message=>{throw Error('PROFILE_PATCH: '+message)}));
  const config=id=>entries.find(e=>e.id===id)?.config??{};
  const has=name=>entries.some(e=>e.name===name&&!e.disabled);
  const event=has('@society0/core/runtime-events'),social=has('@society0/social/social');
  const boot=['resources','tasks','composition-strategies','slot-strategies','module-resolver','config-resolvers','profile-loader','inspector','run-directory'];
  const seed=boot.map(name=>({specifier:'@society0/host/'+name}));
  if(entries.some(e=>['@society0/core/model-pi','@society0/core/model-codex-subscription'].includes(e.name)))seed.push({specifier:'@society0/host/slots'},{specifier:'@society0/core/model-credentials',config:{stores:[]}});
  if(options.branchDirectory)seed.push({specifier:'@society0/host/commands'},{specifier:'@society0/host/run-branch',config:{directory:resolve(options.branchDirectory)}});
  const host=await createHost({baseURL:import.meta.url,boot:seed});
  const metrics={}; let handle,lease,report,mountedTree,consumerFiber;
  try {
    const overrides=[{id:'sqlite',config:{...config('sqlite'),directory,runId}},{id:'artifacts-local',config:{...config('artifacts-local'),directory:join(directory,'artifacts')}},{id:'sqlite-history',config:{...config('sqlite-history'),directory,runId}},{id:'attempts',config:{...config('attempts'),directory:lineageDirectory,lineage}}];
    if(options.patches) overrides.push(...JSON.parse(await readFile(resolve(options.patches),'utf8')));
    const compileStarted=performance.now();
    const plan=await host.ctx.profileLoader.compile(profile,{baseURL:profileURL.href,cliPatches:overrides});
    metrics.profileCompileMs=performance.now()-compileStarted;
    if(options.inspect) return {mode:'inspect',plan:host.ctx.profileLoader.dump(plan),modelRequests:0};
    const uses=name=>plan.flat.some(e=>!e.disabled&&e.name===name);
    const modelEnabled=uses('@society0/core/driver-llm');
    if(uses('@society0/core/model-codex-subscription')&&!options.offline&&!options.observe) throw Error('CODEX_NOT_AUTHORIZED: use --inspect for offline configuration; account login requires separate authorization');
    if(modelEnabled&&!options.offline&&!options.observe&&!options.allowModelRequests) throw Error('MODEL_NOT_AUTHORIZED: use --inspect; live execution must be coordinated by the authorized model owner');
    const databaseEntry=plan.flat.find(e=>e.id==='sqlite');
    const historyEntry=plan.flat.find(e=>e.id==='sqlite-history');
    const source=databaseEntry.binding.identity.source;
    directory=resolve(databaseEntry.config.directory);
    const authorityConfig=plan.flat.find(e=>e.id==='attempts').config;
    const authorityDirectory=resolve(authorityConfig.directory);
    if(options.reopen){
      try{await stat(join(authorityDirectory,'attempts.sqlite'));await stat(join(directory,'world.sqlite'));}
      catch(error){throw Error('REOPEN_AUTHORITY_UNAVAILABLE: preserve '+directory+' and '+authorityDirectory+'; existing run storage and lineage authority are required; no authority was initialized',{cause:error});}
    }else{
      await mkdir(dirname(directory),{recursive:true});
      try{await mkdir(directory);}catch(error){throw Error('FRESH_RUN_EXISTS: '+directory+'; select a new directory or explicitly use --reopen; preserve any missing authority as unknown',{cause:error});}
      await mkdir(dirname(authorityDirectory),{recursive:true});
      try{await mkdir(authorityDirectory);}catch(error){throw Error('FRESH_LINEAGE_EXISTS: '+authorityDirectory+'; select a new lineage directory; existing authority will not be initialized',{cause:error});}
    }
    await options.configureHost?.(host.ctx);
    const [{artifactsMigration},{actorsMigration},{resultsMigration},{threadMigration},{actionsMigration}]=await Promise.all(['artifacts-local','actors','results','thread','actions'].map(n=>import('@society0/core/'+n)));
    const migrations=[artifactsMigration,actorsMigration,resultsMigration(source),threadMigration(source),actionsMigration(source)];
    if(uses('@society0/core/perception'))migrations.push((await import('@society0/core/perception')).perceptionMigration(source));
    const workspaceEntry=plan.flat.find(e=>!e.disabled&&e.name==='@society0/extras/workspace');
    const workspace=workspaceEntry?await import('@society0/extras/workspace'):undefined;
    if(workspace)migrations.push(workspace.workspaceMigration(workspaceEntry.binding.identity.source));
    if(modelEnabled)migrations.push((await import('@society0/core/actor-tools')).actorToolsMigration(source));
    if(event)migrations.push((await import('@society0/core/runtime-events')).eventsMigration);
    if(social)migrations.push((await import('@society0/social/social-repository')).socialMigration,(await import('@society0/social/measurement-repository')).measurementMigration);
    metrics.preparationMs=performance.now()-started;
    const openStarted=performance.now();
    handle=await host.ctx.runDirectory.create({runId,generation:plan,mount:{runEntryId:'run',bootstrapEntryId:'bootstrap'},async prepare(bootstrapCtx,phase){
      mountedTree=phase.tree;
      if(workspace&&!options.reopen){const consumer=await bootstrapCtx.plugin({name:'public.workspace-seed',inject:['initializers'],apply(ctx){ctx.initializers.register(workspace.workspaceInitializer({after:[social?'social.seed':'plain.seed']}))}});await consumer.await();}
      let c;const consumer=await phase.run.plugin({name:'public.prepare',inject:['attempts','sqliteBootstrap','history','database'],apply(ctx){c=ctx}});await consumer.await();
      if(options.reopen) await c.attempts.verifyAuthority(); else await c.attempts.initialize();
      let root;
      if(options.reopen) root=options.observe?await c.history.current():await c.history.activate();
      else root=await c.sqliteBootstrap.create({operationId:'create:'+runId,runId,directory,input:{},migrations,initialFrontier:{codec:event?'events/1':'staged/1',value:'0',logicalTime:event?{time:'0',offset:'0'}:'0',completedCount:'0',pendingAcknowledgements:[]},initialControl:{generation:databaseEntry.config.generation,controlSequence:historyEntry.config.controlSequence,planId:profileURL.href},requiredInitializerIds:[social?'social.seed':'plain.seed',...workspace?['workspace.seed']:[]]});
      const status=await c.history.status();
      return {root,metadata:c.database.metadata,database:c.database,state:status.state};
    }});
    metrics[options.reopen?'recoverMs':'createMs']=performance.now()-openStarted;
    lease=host.ctx.runDirectory.acquire(runId,options.observe?'read':'write');
    let c;consumerFiber=await lease.ctx.plugin({name:'public.consume',inject:['runs','observation','history','database','attempts'],apply(ctx){c=ctx}});await consumerFiber.await();
    const nativeScopes={bootstrapDisposed:!mountedTree.entries.has('bootstrap'),generationMounted:mountedTree.entries.has('generation'),leaseUsesRunContext:lease.ctx===mountedTree.entries.get('run').ctx};
    if(event&&!options.offline&&!options.observe){const consumer=await mountedTree.entries.get('generation').ctx.plugin({name:'public.events',inject:['proposalSource'],async apply(ctx){await ctx.proposalSource.push({id:'event-1',offset:'1',time:'1',payload:{handler:'plain.update',actorId:'alice',attributes:{name:'Alice',message:'public SDK event'}}});ctx.proposalSource.end()}});await consumer.await();await consumer.dispose();}
    const before=await c.history.current();
    const runStarted=performance.now();
    const result=options.observe?{status:'observation-only',advanced:false}:options.offline?{status:'offline-mounted',advanced:false}:await c.runs.run(new AbortController().signal);
    metrics.advanceAndSaveMs=performance.now()-runStarted;
    if(!options.offline&&!options.observe&&result.status!=='completed') throw Error('RUN_'+result.status+': '+JSON.stringify({runId,directory,...result})+'; preserve the run directory and probe the reported complete point before retrying');
    const queryStarted=performance.now();
    const actorCount=(await c.observation.query({sql:'SELECT COUNT(*) AS count FROM s0_actors'})).rows[0].count;
    const observation=await c.observation.query({sql:'SELECT id,attributes FROM s0_actors ORDER BY id'});
    const receipts=await c.observation.query({sql:'SELECT operation_id,actor_id,action,value FROM action_receipts ORDER BY operation_id'});
    const socialObservation=social?{posts:await c.observation.query({sql:'SELECT id,author_id,content FROM social_posts ORDER BY ordinal'}),deliveries:await c.observation.query({sql:'SELECT event_id,post,recipient,sender FROM social_deliveries ORDER BY ordinal'})}:undefined;
    metrics.queryMs=performance.now()-queryStarted;
    const history=c.history.diagnostics();
    const status=await c.observation.status();
    const cut=await c.history.current();
    metrics.memory=process.memoryUsage();metrics.resourceUsage=process.resourceUsage();metrics.totalMs=performance.now()-started;
    const branch=await options.afterRun?.({host,ctx:c,cut,plan,tree:mountedTree,profileURL,directory,runId});
    return report={branch,profile:profileURL.href,runId,directory,reopened:!!options.reopen,root:handle.root,before,cut,result,nativeScopes,lineage:c.attempts.authority(),configuration:host.ctx.profileLoader.dump(plan),source,actorCount,observation,receipts,socialObservation,status,history:c.history.diagnostics(),metrics,modelRequests:options.afterRun?(branch?.modelRequests??null):modelEnabled&&!options.offline&&!options.observe?null:0};
  } finally {const closing=performance.now();await consumerFiber?.dispose();await lease?.release();await handle?.close();const closed=await host.close();if(report){report.close=closed;report.metrics.closeMs=performance.now()-closing;report.metrics.totalMs=performance.now()-started;}}
}

async function main(){
 const {values}=parseArgs({options:{profile:{type:'string'},directory:{type:'string'},'lineage-directory':{type:'string'},lineage:{type:'string'},lock:{type:'string'},patches:{type:'string'},'run-id':{type:'string'},reopen:{type:'boolean'},offline:{type:'boolean'},observe:{type:'boolean'},inspect:{type:'boolean'},help:{type:'boolean'}}});
 if(values.help){console.log('Usage: node run-profile.mjs --profile <profile.json> --directory <run-directory> --lock <module-lock.json> [--run-id <id>] [--lineage-directory <path>] [--lineage <id>] [--patches <patch-array.json>] [--reopen] [--inspect] [--offline] [--observe]\nFresh requires unused world and lineage directories. Reopen requires existing world and authority; fork must retain the source lineage identity and directory. Create or reopen a real SQLite run through ProfileLoader and RunDirectory. --inspect validates and reports effective config without opening a run or requesting a model. --reopen activates existing history and skips seed. --offline mounts native providers, opens the root and observes it without advancing or reading model credentials. --reopen --observe reads the existing source point and live state without advancing or activating a dirty run. Output: one JSON result; failures: stderr and exit 1.');return;}
 if(!values.profile||!values.directory)throw Error('PROFILE_ARGUMENTS: provide --profile and --directory; use --help');
 console.log(JSON.stringify(await runProfile({...values,runId:values['run-id'],lineageDirectory:values['lineage-directory']})));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)await main().catch(error=>{console.error(error);process.exitCode=1});
