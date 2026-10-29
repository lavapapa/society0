import {Context,Service} from '@society0/host';
import {isDeepStrictEqual} from 'node:util';
import {join,resolve} from 'node:path';
import type {BranchPlan,BranchPreparation,BranchSourcePort,BranchPoint} from '@society0/host/run-branch';
import type {RunPreparationPhase} from '@society0/host/run-directory';
import type SqliteHistory from '../sqlite-history/index.js';
import type {ForkRequest} from '../sqlite-history/index.js';
import type {SqliteMigration} from '../sqlite/contracts.js';
import {branchOrigin,ORIGIN_MAX_BYTES} from './branch-origin.js';
import {atomicJson} from '../artifacts-local/files.js';
import '../sqlite-bootstrap/index.js';import '../attempts/index.js';
type Point=Awaited<ReturnType<SqliteHistory['fork']>>;
/** Core owns SQL preparation; the complete native profile owns every target plugin. */
export class SqliteBranchSource extends Service implements BranchSourcePort {
 static inject=['history','attempts','artifacts','database'];
 constructor(ctx:Context){super(ctx,'branchSource')}
 async describe(pointId:string){return this.ctx.history.describe(pointId)}
 async probe(plan:BranchPlan){const result=await this.ctx.history.probeFork({operationId:plan.operationId,targetDirectory:plan.target.targetDirectory});return result.status==='prepared'?{status:'prepared' as const,root:result.point}:result}
 private validateTarget({plan,generation}:BranchPreparation){
  const config=(key:string)=>targetConfig(generation,key);
  const database=config('database'),history=config('history'),artifacts=config('artifacts'),attempts=config('attempts'),authority=this.ctx.attempts.authority();
  validateIdentity(plan,'database',database);validateIdentity(plan,'history',history);
  if(history.controlSequence!==plan.target.controlSequence||resolve(artifacts.directory as string)!==join(plan.target.targetDirectory,'artifacts'))throw Error('BRANCH_TARGET_CONFIGURATION');
  if(resolve(attempts.directory as string)!==resolve(authority.directory)||attempts.lineage!==authority.lineage)throw Error('BRANCH_ATTEMPT_AUTHORITY');
  config('sqliteBootstrap');
 }
 async prepare(request:BranchPreparation):Promise<BranchPoint>{
  const {plan}=request;
  this.validateTarget(request);
  const origin=await branchOrigin(request,this.ctx.database.directory);
  if(plan.target.seedPolicy==='preserve-seed-new-run-address'&&!isDeepStrictEqual(plan.sourceRandom,plan.targetRandom))throw Error('BRANCH_SEED_POLICY_MISMATCH');
  if(!Array.isArray(plan.target.migrations))throw Error('BRANCH_MIGRATIONS_ARRAY');
  const fork:ForkRequest={operationId:plan.operationId,pointId:plan.point.id,targetRunId:plan.target.targetRunId,targetDirectory:plan.target.targetDirectory,initialControl:{generation:plan.target.generation,controlSequence:plan.target.controlSequence,planId:plan.operationId},migrations:plan.target.migrations as unknown as readonly SqliteMigration[]};
  const root:Point=JSON.parse(JSON.stringify(await this.ctx.history.fork(fork)));
  await atomicJson(join(plan.target.targetDirectory,'branch-origin.json'),origin,ORIGIN_MAX_BYTES);return root;
 }
 async open({plan}:BranchPreparation,phase:RunPreparationPhase,root:BranchPoint){
  let ctx:Context|undefined;
  await phase.run.plugin({name:'branch.reopen',inject:['database','history','attempts','sqliteBootstrap'],apply(c){ctx=c}});
  if(!ctx)throw Error('BRANCH_REOPEN_PENDING');
  await ctx.attempts.verifyAuthority();const point=root as Point;
  const reopened=await ctx.sqliteBootstrap.create({operationId:plan.operationId,runId:plan.target.targetRunId,directory:plan.target.targetDirectory,input:{},migrations:[],initialFrontier:point.frontier,initialControl:{generation:point.generation,controlSequence:point.controlSequence,planId:plan.operationId},requiredInitializerIds:[]});
  if(!isDeepStrictEqual(reopened,root))throw Error('BRANCH_REOPEN_ROOT_MISMATCH');
  await ctx.history.activate();return {root,metadata:ctx.database.metadata,database:ctx.database,state:'clean' as const};
 }
}

function targetConfig(generation:BranchPreparation['generation'],key:string){
 const entries=generation.flat.filter(entry=>!entry.disabled&&entry.scope==='run'&&entry.binding?.api.provides.some(service=>service.key===key));
 if(entries.length!==1)throw Error('BRANCH_TARGET_SERVICE '+key);
 return entries[0]!.config as Readonly<Record<string,unknown>>;
}
function validateIdentity(plan:BranchPlan,key:string,value:Readonly<Record<string,unknown>>){
 if(value.runId!==plan.target.targetRunId||value.generation!==plan.target.generation||resolve(value.directory as string)!==plan.target.targetDirectory)throw Error('BRANCH_TARGET_CONFIGURATION '+key);
}
