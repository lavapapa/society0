import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join,basename} from 'node:path';
import {fixture,activation} from './pr10-support.mjs';
import {setup,vector} from './pr09-support.mjs';
import Datasets,{datasetsMigration} from '../.build/extras/dist/datasets/index.js';
import Jsonl from '../.build/extras/dist/dataset-jsonl/index.js';
import Workspace,{workspaceMigration} from '../.build/extras/dist/workspace/index.js';
const high=String.fromCharCode(0xd800);
async function directory(t,prefix='new6-'){
 const root=await mkdtemp(join(tmpdir(),prefix)),intended=await realpath(root);
 t.after(async()=>{
  assert.equal(await realpath(root),intended);
  assert.equal(dirname(intended),await realpath(tmpdir()));assert.ok(basename(intended).startsWith(prefix));
  await rm(intended,{recursive:true});
 });
 return root;
}
function watch(database){
 let transactions=0;
 return {database:new Proxy(database,{
  get(target,property){
   if(property==='transaction')
    return (...args)=>{transactions++;return target.transaction(...args)};
   const value=Reflect.get(target,property,target);
   return typeof value==='function'?value.bind(target):value;
  }
 }),transactions:()=>transactions};
}
function textError(code,field){
 return error=>{
  assert.equal(error.code,code);
  const value=error.cause??error;
  assert.equal(value.field,field);
  assert.ok(String(error.message).includes(field),String(error.message));
  return true;
 };
}
const count=async(f,sql)=>String((await f.read(view=>view.query({sql},{maxRows:1,maxBytes:1024})))[0].n);
const snapshotCount=async(f,sql)=>{const view=await f.ctx.database.snapshot({maxAgeMs:30000});try{return String((await view.query({sql},{maxRows:1,maxBytes:1024}))[0].n)}finally{await view.close()}};

test('NEW-6-T06 Social raw text fields fail before the write transaction',async t=>{
 const root=await directory(t),evidence=await directory(t),attempts=join(evidence,'attempts');await mkdir(attempts);
 const f=await fixture({directory:root,attemptDirectory:attempts});let reservation;
 try{
  reservation=await f.ctx.executionFence.reserve({id:'domain'});
  const watched=watch(f.ctx.database);
  const cases=[
   ['publish',{content:'a'+high+'b'},'content'],
   ['publish',{content:'ok',tags:['ok','b'+high]},'tags[1]'],
   ['publish',{content:'ok',id:'p'+high},'id'],
   ['reply',{postId:'missing',content:'c'+high},'content'],
   ['present',{presentationId:'p'+high,inputId:'i',versions:[],itemIds:['x']},'presentationId'],
   ['present',{presentationId:'p',inputId:'i',versions:[],itemIds:['x'+high]},'itemIds[0]'],
   ['consumeNotifications',{ids:['n'+high]},'ids[0]'],
   ['follow',{actorId:'b'+high},'actorId'],
   ['react',{postId:'missing',kind:'like'+high},'kind']
  ];
  for(const [index,[name,input,field]] of cases.entries())
   await assert.rejects(f.ctx.actions.execute('social.'+name,input,{...activation(f,reservation,'domain-'+index),database:watched.database}),textError('SOCIAL_TEXT_NOT_WELL_FORMED',field));
  assert.equal(watched.transactions(),0);
  assert.equal(await snapshotCount(f,'SELECT count(*) AS n FROM social_posts'),'0');
  assert.equal(await snapshotCount(f,'SELECT count(*) AS n FROM action_receipts'),'0');
  assert.equal(await snapshotCount(f,'SELECT count(*) AS n FROM social_notifications'),'0');
 }finally{await reservation?.release();await f.host.close()}
});

test('NEW-6-T07 Social JSON payloads preserve lone surrogates while raw fields stay validated',async t=>{
 const root=await directory(t),evidence=await directory(t),attempts=join(evidence,'attempts');await mkdir(attempts);
 const f=await fixture({directory:root,attemptDirectory:attempts});let reservation;
 try{
  reservation=await f.ctx.executionFence.reserve({id:'json'});
  const run=(name,input,id)=>f.ctx.actions.execute('social.'+name,input,activation(f,reservation,id));
  const published=await run('publish',{id:'json-post',content:'正文',tags:['t'],metadata:{note:'m'+high}},'json-publish');
  assert.equal(published.status,'completed');
  await run('preferences',{value:{label:'v'+high}},'json-preferences');
  const view=await f.ctx.database.snapshot({maxAgeMs:30000});
  try{
   const post=await f.ctx.socialRepository.get('json-post',view);
   assert.equal(post.metadata.note,'m'+high);
   const [row]=await view.query({sql:"SELECT preferences FROM social_members WHERE id='a'"},{maxRows:1,maxBytes:1024});
   assert.equal(JSON.parse(String(row.preferences)).label,'v'+high);
  }finally{await view.close()}
 }finally{await reservation?.release();await f.host.close()}
});

test('NEW-6-T08 datasets public id rejects malformed text before SQL',async t=>{
 const base=await directory(t),f=await setup([datasetsMigration({consumer:'new6-datasets'})],{base});
 try{
  await f.ctx.plugin(Jsonl);
  await f.ctx.plugin(Datasets,{directory:join(base,'datasets')});
  const object=await f.ctx.datasets.prepare((async function*(){yield {text:'ok'}})(),{maxRecordBytes:65536,frameBytes:1024,maxPendingBytes:262144,signal:new AbortController().signal});
  await assert.rejects(f.write('attach-bad',tx=>f.ctx.datasets.attach(tx,'data'+high,object)),textError('DATASET_ID','id'));
  assert.equal(await count(f,'SELECT count(*) AS n FROM datasets'),'0');
  await f.write('attach-good',tx=>f.ctx.datasets.attach(tx,'data',object));
  assert.equal((await f.read(view=>f.ctx.datasets.describe('data',view))).id,'data');
 }finally{await f.close()}
});

test('NEW-6-T09 memory identity and progress text fail before SQL while JSON body round-trips',async t=>{
 const base=await directory(t),f=await setup([],{base});
 try{
  await assert.rejects(f.write('memory-bad-id',tx=>f.ctx.memoryRepository.append(tx,{id:'m'+high,actorId:'a',threadId:f.threadId,body:{},artifacts:[]})),textError('MEMORY_TEXT_NOT_WELL_FORMED','id'));
  await assert.rejects(f.write('memory-bad-actor',tx=>f.ctx.memoryRepository.append(tx,{id:'m1',actorId:'a'+high,threadId:f.threadId,body:{},artifacts:[]})),textError('MEMORY_TEXT_NOT_WELL_FORMED','actorId'));
  await assert.rejects(f.write('memory-bad-thread',tx=>f.ctx.memoryRepository.append(tx,{id:'m2',actorId:'a',threadId:f.threadId+high,body:{},artifacts:[]})),textError('MEMORY_TEXT_NOT_WELL_FORMED','threadId'));
  await assert.rejects(f.write('memory-bad-progress',tx=>f.ctx.memoryRepository.recordProgress(tx,{runId:f.runId,generation:'g'+high,throughRevision:'0'})),textError('MEMORY_TEXT_NOT_WELL_FORMED','generation'));
  await f.write('memory-json',tx=>f.ctx.memoryRepository.append(tx,{id:'json',actorId:'a',threadId:f.threadId,body:{text:'b'+high},artifacts:[]},vector('json',0)));
  const value=await f.read(view=>f.ctx.memoryRepository.read(view,'json'));
  assert.equal(value.body.text,'b'+high);
  assert.equal(await count(f,'SELECT count(*) AS n FROM memory_experiences'),'1');
  assert.equal(await count(f,'SELECT count(*) AS n FROM memory_index_progress'),'0');
 }finally{await f.close()}
});

test('NEW-6-T10 workspace raw paths fail before writes and rename transactions',async t=>{
 const base=await directory(t),f=await setup([workspaceMigration({consumer:'new6-workspace'})],{base});let reservation;
 try{
  await f.ctx.plugin(Workspace);
  await f.write('workspace-init',tx=>f.ctx.workspace.initialize(tx,'a'));
  reservation=await f.ctx.executionFence.reserve({id:'workspace'});
  const scope={actorId:'a',momentId:'m',query:null};
  const context=(database,operationId)=>({ctx:f.ctx,runId:f.runId,generation:'g',operationId,signal:new AbortController().signal,actorId:'a',momentId:'m',logicalTime:0,database,writeLease:reservation.lease});
  f.ctx.actions.register({name:'new6.workspace.write',effect:'world',inputSchema:{},outputSchema:{},available:async()=>true,async execute(input,c){
   const node=await f.ctx.workspace.writeFile(input.path,(async function*(){yield Buffer.from('x')})(),null,scope,c);
   return {operationId:c.operationId,status:'completed',value:node,original:input,artifacts:[]};
  }});
  f.ctx.actions.register({name:'new6.workspace.rename',effect:'world',inputSchema:{},outputSchema:{},available:async()=>true,async execute(input,c){
   await f.ctx.workspace.mutate(input.mutation,scope,c);
   return {operationId:c.operationId,status:'completed',value:null,original:input,artifacts:[]};
  }});
  await f.ctx.actions.execute('new6.workspace.write',{path:'/workspace/file'},context(f.ctx.database,'workspace-seed'));
  const watchDb=watch(f.ctx.database);
  const parent=await f.read(view=>f.ctx.workspace.stat('/workspace',scope,view)),file=await f.read(view=>f.ctx.workspace.stat('/workspace/file',scope,view));
  await assert.rejects(f.ctx.actions.execute('new6.workspace.write',{path:'/workspace/bad'+high},context(watchDb.database,'workspace-bad-write')),error=>error.code==='WORKSPACE_PATH');
  await assert.rejects(f.ctx.actions.execute('new6.workspace.rename',{mutation:{kind:'rename',from:'/workspace/file',to:'/workspace/bad'+high,expected:file,targetExpected:null,expectedParent:parent}},context(watchDb.database,'workspace-bad-rename')),error=>error.code==='WORKSPACE_PATH');
  assert.equal(watchDb.transactions(),0);
  assert.equal(await count(f,"SELECT count(*) AS n FROM workspace_nodes WHERE path LIKE '/workspace/bad%'"),'0');
  assert.ok(await f.read(view=>f.ctx.workspace.visible('/workspace/file',scope,view)));
 }finally{await reservation?.release();await f.close()}
});
