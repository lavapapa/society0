import {createRequire} from 'node:module';import {resolve,join,dirname} from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';import {mkdtemp,readFile,writeFile} from 'node:fs/promises';import {tmpdir} from 'node:os';
export const stage=fileURLToPath(new URL('../.build',import.meta.url));const require=createRequire(stage+'/package.json');
export const resolvePackage=specifier=>require.resolve(specifier);
export const load=specifier=>import(pathToFileURL(require.resolve(specifier)));
export const part=(pkg,entry)=>load('@society0/'+pkg+'/'+entry);
export const req=operationId=>({operationId,maxDurationMs:15000,maxStatements:100,maxInputBytes:1048576,maxChangedRows:100,maxCapturedBytes:4194304});
export async function fullProfile(home,runId,directory,generation,controlSequence,seed='study-1'){
 const packages={};for(const name of ['host','core'])packages['@society0/'+name]=JSON.parse(await readFile(resolvePackage('@society0/'+name+'/plugin.meta.json'),'utf8')).source;
 const lock=join(home,runId+'.lock.json');await writeFile(lock,JSON.stringify({lockfile:lock,packages}));
 const entry=(id,name,config={})=>({id,name,config});const random=id=>entry(id,'@society0/core/random',{algorithm:'philox4x32-10/v1',seed});
 const profile={format:'society-profile/v2',bundles:[],patches:[{insert:[{id:'run-scope',name:'run',group:true,lifetime:'run',config:[
  entry('slots','@society0/host/slots'),entry('fence','@society0/core/execution-fence'),
  entry('database','@society0/core/sqlite',{directory,runId,generation,maxTransactionMs:20000,maxQueryRows:10001}),
  entry('artifacts','@society0/core/artifacts-local',{directory:join(directory,'artifacts')}),entry('initializers','@society0/core/initializers'),
  entry('bootstrap','@society0/core/sqlite-bootstrap',{maxTransactionMs:15000}),
  entry('history','@society0/core/sqlite-history',{directory,runId,generation,controlSequence}),
  entry('attempts','@society0/core/attempts',{directory:join(home,'lineage'),lineage:'lineage-1'}),
  entry('replay','@society0/core/replay',{}),entry('observation','@society0/core/observation',{pageSize:100,bufferSize:2,pollIntervalMs:10000}),
  {id:'bootstrap-scope',name:'bootstrap',group:true,lifetime:'bootstrap',config:[random('bootstrap-random')]},
  {id:'generation-scope',name:'generation',group:true,lifetime:'generation',config:[random('generation-random'),entry('clock','@society0/core/clock-ticks')]}
 ]}]}],configSources:[],lockfile:pathToFileURL(lock).href};
 const path=join(home,runId+'.profile.json');await writeFile(path,JSON.stringify(profile));return {profile,baseURL:pathToFileURL(path).href};
}
export async function fixture(){
 const H=await load('@society0/host');const host=await H.createHost({boot:[]});const home=await mkdtemp(join(tmpdir(),'pr11-'));
 for(const P of [H.OwnedResources,H.TaskSupervisor])await host.ctx.plugin(P);
 for(const name of ['module-resolver','config-resolvers','profile-loader','inspector','composition-strategies','slot-strategies','commands','run-directory','observation-gateway'])await host.ctx.plugin((await part('host',name)).default,name==='module-resolver'?{baseURL:pathToFileURL(join(stage,'consumer.mjs')).href}:{});
 const directory=join(home,'source');const selected=await fullProfile(home,'source',directory,'g1','0');const generation=await host.ctx.profileLoader.compile(selected.profile,{baseURL:selected.baseURL});const Artifacts=await part('core','artifacts-local');let ctx,root,bootstrapValue;
 const handle=await host.ctx.runDirectory.create({runId:'source',generation,mount:{runEntryId:'run-scope',bootstrapEntryId:'bootstrap-scope'},async prepare(bootstrap,phase){
  let seedCtx;await bootstrap.plugin({inject:['initializers','random'],apply(x){seedCtx=x;x.initializers.register({id:'seed',after:[],async initialize(context){bootstrapValue=x.random.at({run:context.runId,actor:'bootstrap',operation:'seed',stream:'note',index:'0'});await context.database.transaction(context.writeLease,req('seed'),async tx=>{for(let i=0;i<5;i++)await tx.execute({sql:'INSERT INTO note VALUES(?,?)',parameters:[BigInt(i),i===0?'seed-'+bootstrapValue.uint32:'row-'+i]})});return {operationIds:['seed']}}})}});if(!seedCtx)throw Error('BOOTSTRAP_RANDOM_PENDING');
  await phase.run.plugin({inject:['attempts','sqliteBootstrap','database','history','executionFence','artifacts','replay','observation','tasks'],apply(x){ctx=x}});if(!ctx)throw Error('RUN_SERVICES_PENDING');await ctx.attempts.initialize();
  root=await ctx.sqliteBootstrap.create({operationId:'create-source',runId:'source',directory,input:{},migrations:[Artifacts.artifactsMigration,{owner:'note',from:0,to:1,after:['artifacts-local'],source:{consumer:'pr11'},statements:[{sql:'CREATE TABLE note(id INTEGER PRIMARY KEY,v TEXT NOT NULL)'}]}],initialFrontier:{codec:'staged/1',value:'0',logicalTime:'0',completedCount:'0',pendingAcknowledgements:[]},initialControl:{generation:'g1',controlSequence:'0',planId:'source-plan'},requiredInitializerIds:['seed']});
  return {root,metadata:ctx.database.metadata,database:ctx.database,state:'clean'};
 }});
 return {home,host,ctx,root,directory,handle,bootstrapValue,async write(id,value){const r=await ctx.executionFence.reserve({id});try{return await ctx.database.transaction(r.lease,req(id),tx=>tx.execute({sql:'UPDATE note SET v=? WHERE id=0',parameters:[value]}))}finally{await r.release()}},close:()=>host.close()};
}
