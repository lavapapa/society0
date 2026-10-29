import type {SqliteValue, SqliteRow} from '@society0/sqlite-native';
export type {SqliteValue, SqliteRow};
export interface SqliteStatement {readonly sql:string;readonly parameters?:readonly SqliteValue[]}
export interface QueryBounds {readonly maxRows:number;readonly maxBytes:number}
export interface SqliteSchemaVersion {readonly owner:string;readonly version:number}
export interface SqliteMigration {readonly owner:string;readonly from:number;readonly to:number;readonly after:readonly string[];readonly source:Readonly<Record<string,unknown>>;readonly statements:readonly SqliteStatement[]}
export interface TransactionRequest {readonly operationId:string;readonly expectedRevision?:string;readonly maxDurationMs:number;readonly maxStatements:number;readonly maxInputBytes:number;readonly maxChangedRows:number;readonly maxCapturedBytes:number}
export interface CommitReceipt {readonly operationId:string;readonly revision:string}
export type CommitProbe={status:'committed';receipt:CommitReceipt}|{status:'not-committed'}|{status:'unknown';reason:string};
export interface SqliteTransaction {readonly id:string;readonly operationId:string;readonly runId:string;readonly baseRevision:string;query(statement:SqliteStatement,bounds:QueryBounds):Promise<readonly SqliteRow[]>;execute(statement:SqliteStatement):Promise<{changes:bigint;lastInsertRowid:bigint}>;executeMany(sql:string,parameters:readonly (readonly SqliteValue[])[],bounds:{maxRows:number;maxInputBytes:number}):Promise<{changes:bigint}>}
export interface BlobRequest {table:string;column:string;rowid:bigint;offset:number;length:number;maxBytes:number}
export interface SqliteReadView {readonly id:string;readonly runId:string;readonly revision:string;readonly schema:readonly SqliteSchemaVersion[];query(statement:SqliteStatement,bounds:QueryBounds):Promise<readonly SqliteRow[]>;readBlobRange(request:BlobRequest):Promise<Uint8Array>}
export interface SqliteSnapshot extends SqliteReadView {close():Promise<void>}
export interface RuntimeFrontier {readonly codec:string;readonly value:unknown;readonly logicalTime:unknown;readonly completedCount:string;readonly pendingAcknowledgements:readonly {source:string;reservationId:string;proposalId:string}[]}
export interface SqliteRuntimeMetadata {read(snapshot:SqliteSnapshot):Promise<RuntimeFrontier>;complete(tx:SqliteTransaction,frontier:RuntimeFrontier,requiredOperations:readonly string[]):Promise<void>}
export interface DatabaseOptions {directory:string;runId:string;busyTimeoutMs?:number;maxQueuedBytes?:number;maxPending?:number;maxTransactionMs?:number;maxQueryBytes?:number;maxQueryRows?:number;maxSnapshots?:number;maxSnapshotAgeMs?:number;maxInputBytes?:number;maxInlineBytes?:number}
export function fault(code:string,message=code):Error {return Object.assign(new Error(`${code}: ${message}`),{code})}
export function positive(value:number,name:string):number {if(!Number.isSafeInteger(value)||value<=0)throw fault('SQLITE_BUDGET',name+' must be a positive safe integer');return value}
function scalar(value:unknown):value is null|undefined|number|bigint|boolean {
 return value===null||value===undefined||typeof value==='number'||typeof value==='bigint'||typeof value==='boolean';
}
export function inputBytes(value:unknown):number {
 if(scalar(value))return 8;
 if(typeof value==='string')return Buffer.byteLength(value);
 if(value instanceof SharedArrayBuffer)return value.byteLength;
 if(value instanceof Uint8Array)return value.byteLength;
 if(Array.isArray(value))return value.reduce((n,v)=>n+inputBytes(v),16);
 if(typeof value==='object')return Object.entries(value).reduce((n,[k,v])=>n+Buffer.byteLength(k)+inputBytes(v),16);
 throw fault('SQLITE_ARGUMENT','RPC accepts data only');
}

/** Structured clone copies backing stores, so views must lose unrelated backing bytes. */
export function rpcData(value:any):any {
 if(value instanceof SharedArrayBuffer)return value;
 if(value instanceof Uint8Array){const copy=new Uint8Array(value.byteLength);copy.set(value);return copy}
 if(Array.isArray(value))return value.map(rpcData);
 if(value!==null&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,rpcData(item)]));
 return value;
}

function rpcScalarBytes(value:unknown):number {return value===null||value===undefined?8:16}
/** Conservative structured-clone storage accounting, including UTF-16 and container slots. */
export function rpcBytes(value:unknown):number {
 if(scalar(value))return rpcScalarBytes(value);
 if(typeof value==='string')return 16+value.length*2;
 if(value instanceof SharedArrayBuffer)return 32+value.byteLength;
 if(value instanceof Uint8Array)return 32+value.byteLength;
 if(Array.isArray(value))return value.reduce((n,item)=>n+8+rpcBytes(item),24);
 if(typeof value==='object')return Object.entries(value).reduce((n,[key,item])=>n+16+key.length*2+rpcBytes(item),32);
 throw fault('SQLITE_ARGUMENT','RPC accepts data only');
}
