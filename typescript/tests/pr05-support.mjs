import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname} from 'node:path';
import {randomUUID} from 'node:crypto';
import * as hostApi from '../.build/host/dist/index.js';
import CompositionStrategies from '../.build/host/dist/composition-strategies/index.js';
import SlotStrategies from '../.build/host/dist/slot-strategies/index.js';
import Slots from '../.build/host/dist/slots/index.js';
import ArtifactsLocal,{artifactsMigration} from '../.build/core/dist/artifacts-local/index.js';
import SqliteHistory from '../.build/core/dist/sqlite-history/index.js';
import Attempts from '../.build/core/dist/attempts/index.js';
import SqliteDatabase,{Database} from '../.build/core/dist/sqlite/index.js';
import ExecutionFence,{ExecutionGate} from '../.build/core/dist/execution-fence/index.js';
import Initializers from '../.build/core/dist/initializers/index.js';
import SqliteBootstrap from '../.build/core/dist/sqlite-bootstrap/index.js';
export const request=operationId=>({operationId,maxDurationMs:20000,maxStatements:1000,maxInputBytes:1048576,maxChangedRows:10000,maxCapturedBytes:4194304});
export const frontier=n=>({codec:'step/1',value:n,logicalTime:n,completedCount:String(n),pendingAcknowledgements:n?[{source:'input',reservationId:'r'+n,proposalId:'p'+n}]:[]});
export const targetOf=f=>join(dirname(f.directory),'pr05-branch-'+randomUUID());
export async function* body(chunks){for(let i=0;i<chunks;i++)yield Buffer.alloc(65536,i%251)}
export async function fixture(options={}) {
 const directory=options.directory??await mkdtemp(join(tmpdir(),'pr05-world-'));
 const generation=options.generation??'g1';const runId=options.runId??'source',lineage=options.lineage??'lineage';
 const authorityDirectory=options.authorityDirectory??join(dirname(directory),'pr05-lineage-'+randomUUID());
 const host=await hostApi.createHost({boot:[]}),ctx=host.ctx;
 const f={host,ctx,directory,runId,lineage,authorityDirectory,seedCalls:0,options,operations:[]};
 try {
  await ctx.plugin(hostApi.OwnedResources);
  await ctx.plugin(hostApi.TaskSupervisor,{concurrency:8,maxPending:128,maxBytes:16777216});
  await ctx.plugin(CompositionStrategies);await ctx.plugin(SlotStrategies);await ctx.plugin(Slots);
  await ctx.plugin(ExecutionFence);await ctx.plugin(ArtifactsLocal,{directory:join(directory,'artifacts')});
  await ctx.plugin(SqliteDatabase,{directory,runId,generation,maxTransactionMs:30000,...options.database});
  await ctx.plugin(Initializers);await ctx.plugin(SqliteBootstrap,{maxTransactionMs:20000});
  await ctx.plugin(SqliteHistory,{directory,runId,generation,controlSequence:options.controlSequence??'0',...options.history});
  if(options.attempts)await ctx.plugin(Attempts,{directory:authorityDirectory,lineage,...(options.attemptChunkBytes===undefined?{}:{chunkBytes:options.attemptChunkBytes})});
  Object.assign(f,{database:ctx.database,fence:ctx.executionFence,history:ctx.history,artifacts:ctx.artifacts});
  if(options.onPhase)ctx.history.bindIOObserver(options.onPhase);
  await ctx.plugin({inject:['initializers','artifacts',...(options.attempts?['attempts']:[])],apply(ctx){ctx.initializers.register({id:'seed',after:[],async initialize(context){
   f.seedCalls++;
   if(options.attempts)await ctx.attempts.initialize();
   if(options.bodyChunks)f.body=await ctx.artifacts.stage(body(options.bodyChunks),'application/octet-stream');
   if(options.unknownAttempt){f.attemptIdentity={lineage,operationId:'external-seed',attempt:0};f.claim=await ctx.attempts.claim(f.attemptIdentity,{body:f.body},f.body?[f.body]:[]);if(f.claim.status!=='send-permitted')throw Error('fixture authority did not grant first send');await ctx.attempts.markUnknown(f.attemptIdentity,'response lost');}
   await context.database.transaction(context.writeLease,request('seed'),async tx=>{
    await tx.execute({sql:'INSERT INTO note VALUES(?,?)',parameters:[1n,options.rows?'x'.repeat(4000):'root']});
    await tx.execute({sql:'INSERT INTO audit VALUES(1,0)'});
    await tx.execute({sql:'INSERT INTO child VALUES(1,1)'});
    if(f.body){await ctx.artifacts.reference(tx,f.body);await tx.execute({sql:'INSERT INTO note_body VALUES(1,?)',parameters:[f.body.id]});}
   });
   const operationIds=['seed'];
   for(let first=2;first<=(options.rows??1);first+=128){const rows=[];for(let i=first;i<=Math.min(first+127,options.rows);i++)rows.push([BigInt(i),'x'.repeat(4000)]);const operationId='seed-batch-'+first;await context.database.transaction(context.writeLease,request(operationId),tx=>tx.executeMany('INSERT INTO note VALUES(?,?)',rows,{maxRows:128,maxInputBytes:1048576}));operationIds.push(operationId)}
   return {operationIds,artifactIds:f.body?[f.body.id]:[]};
  }})}});
  return f;
 } catch(error) {await host.close().catch(()=>{});throw error}
}
export async function prepare(f){return f.ctx.sqliteBootstrap.create({operationId:'create',runId:f.runId,directory:f.directory,input:{},migrations:[artifactsMigration,{owner:'world',from:0,to:1,after:['artifacts-local'],source:{version:1},statements:[
 {sql:'CREATE TABLE note(id INTEGER PRIMARY KEY,v TEXT)'},
 {sql:'CREATE TABLE audit(id INTEGER PRIMARY KEY,n INTEGER NOT NULL)'},
 {sql:'CREATE TABLE child(id INTEGER PRIMARY KEY,parent INTEGER NOT NULL REFERENCES note(id) ON DELETE CASCADE)'},
 {sql:'CREATE TABLE note_body(note INTEGER PRIMARY KEY REFERENCES note(id) ON DELETE CASCADE,artifact TEXT NOT NULL REFERENCES s0_artifacts(id))'},
 {sql:'CREATE TRIGGER note_changed AFTER UPDATE ON note BEGIN UPDATE audit SET n=n+1 WHERE id=1; END'},
 {sql:'CREATE TRIGGER note_deleted AFTER DELETE ON note BEGIN UPDATE audit SET n=n+10 WHERE id=1; END'}
 ]}],initialFrontier:frontier(0),initialControl:{generation:'g1',controlSequence:'0',planId:'plan'},requiredInitializerIds:['seed']})}
export function advance(f,n,statement={sql:'UPDATE note SET v=? WHERE id=1',parameters:['v'+n]},checkpoint=frontier(n)) {
 const operationId='step-'+n;
 const physical=f.ctx.tasks.run('driver.'+operationId,async()=>{const reservation=await f.fence.reserve({id:operationId});try{return await f.database.transaction(reservation.lease,request(operationId),async tx=>{await tx.execute(statement);await f.database.metadata.complete(tx,checkpoint,f.operations)})}finally{await reservation.release()}});
 f.lastTask=physical;
 if(!f.bound){f.history.bindQuiescence(()=>f.lastTask.settled);f.bound=true}
 void physical.then(()=>f.operations.push(operationId),()=>{});
 return physical;
}
export async function publish(f,parent,id='point-1',operationId='publish') {await f.lastTask.settled;const lease=await f.fence.seal();const point=await f.history.publish(lease,{operationId,id,parentId:parent.id,expectedRevision:await f.database.readRevision(),requiredOperations:[...f.operations]});return {point,lease}}
export async function inspect(directory,runId){let db;const gate=new ExecutionGate({runId,generation:'g1',maxAcceptedCommits:10,readRevision:()=>db.readRevision()});db=await Database.open({directory,runId,maxTransactionMs:30000},gate);try{const s=await db.snapshot({maxAgeMs:30000});try{return {revision:s.revision,frontier:await db.metadata.read(s),notes:await s.query({sql:'SELECT id,v FROM note WHERE id=1'},{maxRows:1,maxBytes:8192}),count:(await s.query({sql:'SELECT count(*) AS n FROM note'},{maxRows:1,maxBytes:256}))[0].n,audit:(await s.query({sql:'SELECT n FROM audit'},{maxRows:1,maxBytes:256}))[0].n,children:(await s.query({sql:'SELECT count(*) AS n FROM child'},{maxRows:1,maxBytes:256}))[0].n}}finally{await s.close()}}finally{await db.close()}}
