import {parentPort,workerData} from 'node:worker_threads';
import {join} from 'node:path';
import {mkdirSync,existsSync} from 'node:fs';
import {SqliteConnection,configureHeapLimit} from '@society0/sqlite-native';
import {fault,inputBytes,rpcData,type TransactionRequest,type SqliteSchemaVersion} from './contracts.js';
import {applySchemaMigrations,readSchemaVersions} from './schema-native.js';
const {options,control,probeOnly}=workerData;
configureHeapLimit(512*1024*1024);
const path=join(options.directory,'world.sqlite');
mkdirSync(options.directory,{recursive:true});
const reopened=existsSync(path);
const db=new SqliteConnection({path,readOnly:!!probeOnly,control,maxInlineBytes:options.maxInlineBytes,timeoutMs:options.busyTimeoutMs,maxInputBytes:options.maxInputBytes});
if(!probeOnly&&!reopened){
 db.execSchema(`CREATE TABLE IF NOT EXISTS s0_identity(id INTEGER PRIMARY KEY CHECK(id=1),run_id TEXT NOT NULL,revision TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS s0_operations(operation_id TEXT PRIMARY KEY NOT NULL,revision TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS s0_schema(owner TEXT PRIMARY KEY NOT NULL,version INTEGER NOT NULL,source TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS s0_frontier(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);`);
 const rows=db.query('SELECT run_id FROM s0_identity',[],{maxRows:1,maxBytes:4096});
 if(!rows.length){db.begin();db.execute('INSERT INTO s0_identity VALUES (1,?,?)',[options.runId,'0']);db.commit()}
}
if(!probeOnly&&String(db.query('SELECT run_id FROM s0_identity WHERE id=1',[],{maxRows:1,maxBytes:4096})[0]?.run_id)!==options.runId)throw fault('SQLITE_RUN_ID');
const revision=()=>String(db.query('SELECT revision FROM s0_identity WHERE id=1',[],{maxRows:1,maxBytes:256})[0]!.revision);
const schema=()=>readSchemaVersions(db);
const counters={commits:0,fsyncs:0};
let active:{id:string;request:TransactionRequest;statements:number;framework:number;input:number;changed:bigint;baseline:bigint;failed:boolean;timer:ReturnType<typeof setTimeout>}|undefined;
const snapshots=new Map<string,{db:SqliteConnection;timer:ReturnType<typeof setTimeout>}>();
let tables:string[]|undefined;let schemaEnumerations=0;
function rollback(){if(!active)return;clearTimeout(active.timer);try{db.rollback()}finally{active=undefined}}
function tx(args:any){if(!active||args.tx!==active.id)throw fault('SQLITE_TX_EXPIRED');if(active.failed)throw fault('SQLITE_TX_FAILED');if(Atomics.load(control,0))throw fault('SQLITE_CANCELLED');return active}
const changes=()=>BigInt(String(db.query('SELECT total_changes() AS n',[],{maxRows:1,maxBytes:256})[0]!.n));
function changed(state:NonNullable<typeof active>){state.changed=changes()-state.baseline;if(state.changed>BigInt(state.request.maxChangedRows))throw fault('SQLITE_CHANGED_ROWS',`${state.changed} > maxChangedRows ${state.request.maxChangedRows}`)}
function statement(args:any,count=1){const state=tx(args);const framework=args.framework===true;if(framework){state.framework+=count;const limit=state.request.maxFrameworkStatements??0;if(state.framework>limit)throw fault('SQLITE_TX_BUDGET',`maxFrameworkStatements ${state.framework} > ${limit}`)}else{state.statements+=count;if(state.statements>state.request.maxStatements)throw fault('SQLITE_TX_BUDGET',`maxStatements ${state.statements} > ${state.request.maxStatements}`)}state.input+=inputBytes(args.statement??{sql:args.sql,parameters:args.parameters});if(state.input>state.request.maxInputBytes)throw fault('SQLITE_TX_BUDGET',`maxInputBytes ${state.input} > ${state.request.maxInputBytes}`);return state}
function bounds(value:any){if(!Number.isSafeInteger(value.maxRows)||value.maxRows<1||value.maxRows>options.maxQueryRows||!Number.isSafeInteger(value.maxBytes)||value.maxBytes<1||value.maxBytes>options.maxQueryBytes)throw fault('SQLITE_QUERY_BUDGET');return value}
function reader(id:string){const snapshot=snapshots.get(id);if(!snapshot)throw fault('SQLITE_SNAPSHOT_EXPIRED');return snapshot.db}
function closeSnapshot(id:string){const snapshot=snapshots.get(id);if(!snapshot)return;clearTimeout(snapshot.timer);snapshot.db.close();snapshots.delete(id)}
function ready(){return {revision:revision(),schema:schema(),reopened}}
function migrate(args:any){
 if(active)throw fault('SQLITE_BUSY');if(revision()!=='0')throw fault('SCHEMA_LIVE');
 const installed=applySchemaMigrations(db,args.migrations);tables=undefined;return installed;
}
function begin(args:any){
 if(active)throw fault('SQLITE_BUSY');
 if(db.query('SELECT revision FROM s0_operations WHERE operation_id=?',[args.request.operationId],{maxRows:1,maxBytes:256}).length)throw fault('OPERATION_EXISTS');
 const baseRevision=revision();if(args.request.expectedRevision!==undefined&&args.request.expectedRevision!==baseRevision)throw fault('SQLITE_STALE_REVISION');
 if(!tables){schemaEnumerations++;const rows=db.query("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",[],{maxRows:10000,maxBytes:4194304});const fts=rows.filter(r=>/^CREATE\s+VIRTUAL\s+TABLE\s+.+\s+USING\s+fts5/i.test(String(r.sql??''))).map(r=>String(r.name));const shadows=new Set(fts.flatMap(base=>['_data','_idx','_content','_docsize','_config'].map(suffix=>base+suffix)));tables=rows.filter(r=>{const name=String(r.name);return !fts.includes(name)&&!shadows.has(name)}).map(r=>String(r.name))}
 db.startSession(tables);
 try{db.begin({maxDurationMs:args.request.maxDurationMs,maxCapturedBytes:args.request.maxCapturedBytes});active={id:args.tx,request:args.request,statements:0,framework:0,input:0,changed:0n,baseline:changes(),failed:false,timer:setTimeout(()=>rollback(),args.request.maxDurationMs)}}
 catch(error){if(db.state().transaction)db.rollback();else db.retireSession();throw error}
 return {baseRevision};
}
function execute(args:any){const state=statement(args);const result=db.execute(args.statement.sql,args.statement.parameters);changed(state);return result}
function many(args:any){const state=statement(args,args.parameters.length);const result=db.executeMany(args.sql,args.parameters,args.bounds);changed(state);return result}
function query(args:any){statement(args);return db.query(args.statement.sql,args.statement.parameters,bounds(args.bounds))}
function metadataComplete(args:any){
 tx(args);
 for(const id of args.requiredOperations){const sql='SELECT revision FROM s0_operations WHERE operation_id=?';statement({tx:args.tx,statement:{sql,parameters:[id]}});if(!db.query(sql,[id],{maxRows:1,maxBytes:256}).length)throw fault('SQLITE_REQUIRED_OPERATION',id)}
 const encoded=JSON.stringify(args.frontier);statement({tx:args.tx,statement:{sql:'frontier',parameters:[encoded]}});db.execute('INSERT INTO s0_frontier VALUES (1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',[encoded]);changed(tx(args));
}
function prepareCommit(args:any){const state=tx(args);const next=String(BigInt(revision())+1n);db.execute('UPDATE s0_identity SET revision=? WHERE id=1',[next]);db.execute('INSERT INTO s0_operations VALUES (?,?)',[state.request.operationId,next]);changed(state);return {state,next}}
function phase(error:unknown,commitPhase:string,receipt?:unknown):never{throw Object.assign(error instanceof Error?error:new Error(String(error)),{commitPhase,receipt})}
function stillOpen(){try{return db.state().autocommit===false}catch{return false}}
function commit(args:any){
 let prepared;try{prepared=prepareCommit(args)}catch(error){phase(error,'prepare')}
 const {state,next}=prepared!;try{db.commit()}catch(error){phase(error,stillOpen()?'prepare':'commit')}
 clearTimeout(state.timer);active=undefined;
 counters.commits++;if(options.synchronous!=='normal')counters.fsyncs++;
 const receipt={operationId:state.request.operationId,revision:next,counters:{...counters}};
 try{mkdirSync(join(options.directory,'segments'),{recursive:true});db.exportSegment(join(options.directory,'segments',next+'.changeset'),{maxBytes:state.request.maxCapturedBytes*2+65536})}catch(error){phase(error,'segment',receipt)}
 return receipt;
}
function probe(args:any){
 if(active)return {status:'unknown',reason:'writer active'};
 const row=db.query('SELECT revision FROM s0_operations WHERE operation_id=?',[args.operationId],{maxRows:1,maxBytes:256})[0];return row?{status:'committed',receipt:{operationId:args.operationId,revision:String(row.revision)}}:{status:'not-committed'};
}
function snapshot(args:any){
 const read=new SqliteConnection({path,readOnly:true,control:new Int32Array(args.control),maxInlineBytes:options.maxInlineBytes,maxInputBytes:options.maxInputBytes,timeoutMs:options.busyTimeoutMs});
 try{read.beginSnapshot({maxAgeMs:args.maxAgeMs});const rev=String(read.query('SELECT revision FROM s0_identity WHERE id=1',[],{maxRows:1,maxBytes:256})[0]!.revision);const versions=read.query('SELECT owner,version FROM s0_schema ORDER BY owner',[],{maxRows:10000,maxBytes:1048576}).map(r=>({owner:String(r.owner),version:Number(r.version)}));snapshots.set(args.id,{db:read,timer:setTimeout(()=>closeSnapshot(args.id),args.maxAgeMs)});return {revision:rev,schema:versions}}catch(error){read.close();throw error}
}
function snapshotQuery(args:any){return reader(args.id).query(args.statement.sql,args.statement.parameters,bounds(args.bounds))}
function blob(args:any){if(args.request.maxBytes>options.maxQueryBytes)throw fault('SQLITE_QUERY_BUDGET');return reader(args.id).readBlobRange(args.request)}
function snapshotClose(args:any){closeSnapshot(args.id)}
function state(){return {...db.state(),schemaEnumerations,...counters}}
function close(){rollback();for(const id of snapshots.keys())closeSnapshot(id);db.close()}
const operations=new Map<string,(args:any)=>unknown>([
 ['ready',ready],['revision',revision],['migrate',migrate],['begin',begin],
 ['execute',execute],['many',many],['query',query],['metadata',metadataComplete],
 ['commit',commit],['rollback',rollback],['probe',probe],['snapshot',snapshot],
 ['snapshotQuery',snapshotQuery],['blob',blob],['snapshotClose',snapshotClose],['state',state],['close',close],
]);
function dispatch(op:string,args:any):unknown {
 const operation=operations.get(op);
 if(!operation)throw fault('SQLITE_RPC_OPERATION',op);
 return operation(args);
}
parentPort!.on('message',({id,op,args})=>{
 try{const value=dispatch(op,args);parentPort!.postMessage({id,value:rpcData(value)});if(op==='close')parentPort!.close()}
 catch(error){if(active&&['query','execute','many','metadata','commit'].includes(op))active.failed=true;const e=error as Error&{code?:string;sqliteCode?:number};const diagnostic=op==='snapshotQuery'||op==='blob'?snapshots.get(args.id)?.db:db;const stopReason=diagnostic?.state().stopReason;const code=e.sqliteCode===9&&stopReason==='capture-budget'?'SQLITE_CAPTURE_BUDGET':e.code??'SQLITE_ERROR';parentPort!.postMessage({id,error:{message:e.sqliteCode===9&&stopReason==='capture-budget'?code+': '+e.message:e.message,code,sqliteCode:e.sqliteCode,stopReason,commitPhase:(e as any).commitPhase,receipt:(e as any).receipt}})}
});
