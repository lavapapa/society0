import {open,readFile,rename,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import type {SqliteConnection} from '@society0/sqlite-native';
import type {SeedManifest} from '../initializers/index.js';
import {fault,type RuntimeFrontier} from '../sqlite/contracts.js';
export interface Journal {operationId:string;runId:string;status:'preparing'|'prepared';seed?:SeedManifest}
export async function saveBootstrapJournal(directory:string,record:Journal,initial=false){
 await mkdir(directory,{recursive:true});const path=join(directory,initial?'bootstrap.json':'bootstrap.next');const file=await open(path,'wx');try{await file.writeFile(JSON.stringify(record));await file.sync()}finally{await file.close()}
 if(!initial)await rename(path,join(directory,'bootstrap.json'));const dir=await open(directory,'r');try{await dir.sync()}finally{await dir.close()}
}
export async function readBootstrapJournal(directory:string):Promise<Journal|undefined>{try{return JSON.parse(await readFile(join(directory,'bootstrap.json'),'utf8'))}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return;throw error}}
/** 调用方已在私有目标持久化身份、frontier及 operationId/frontier 回执；这里核实后封存 prepared 事实。 */
export async function recordForkPrepared(db:SqliteConnection,directory:string,request:{operationId:string;runId:string;initialFrontier:RuntimeFrontier},artifactIds:readonly string[]=[]):Promise<SeedManifest>{
 if(!db.state().autocommit)throw fault('FORK_PREPARATION_ACTIVE_TRANSACTION');
 const bounds={maxRows:1,maxBytes:1048576};const identity=db.query('SELECT run_id,revision FROM s0_identity WHERE id=1',[],bounds)[0];if(identity?.run_id!==request.runId)throw fault('SQLITE_RUN_ID');
 const frontier=db.query('SELECT value FROM s0_frontier WHERE id=1',[],bounds)[0];if(!frontier||!isDeepStrictEqual(JSON.parse(String(frontier.value)),request.initialFrontier))throw fault('FORK_FRONTIER_MISMATCH');
 const operationId=request.operationId+'/frontier';const receipt=db.query('SELECT revision FROM s0_operations WHERE operation_id=?',[operationId],bounds)[0];if(!receipt||receipt.revision!==identity.revision)throw fault('FORK_FRONTIER_RECEIPT');
 const seed:SeedManifest={initializerIds:[],operationIds:[operationId],artifactIds:[...artifactIds]};await saveBootstrapJournal(directory,{operationId:request.operationId,runId:request.runId,status:'preparing'},true);await saveBootstrapJournal(directory,{operationId:request.operationId,runId:request.runId,status:'prepared',seed});return seed;
}
