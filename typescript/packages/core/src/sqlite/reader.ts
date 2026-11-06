import {parentPort,workerData} from 'node:worker_threads';
import {join} from 'node:path';
import {SqliteConnection} from '@society0/sqlite-native';
import {fault,type QueryBounds} from './contracts.js';
const {options}=workerData;
const path=join(options.directory,'world.sqlite');
type Slot={db:SqliteConnection;snapshot?:{id:string;timer:ReturnType<typeof setTimeout>}};
const slots:Slot[]=[];
function open(){return new SqliteConnection({path,readOnly:true,control:new Int32Array(new SharedArrayBuffer(4)),maxInlineBytes:options.maxInlineBytes,maxInputBytes:options.maxInputBytes,timeoutMs:options.busyTimeoutMs,synchronous:options.synchronous,cacheSize:options.cacheSize})}
function free(){let slot=slots.find(candidate=>!candidate.snapshot);if(!slot){slot={db:open()};slots.push(slot)}return slot}
function find(id:string){const slot=slots.find(candidate=>candidate.snapshot?.id===id);if(!slot)throw fault('SQLITE_SNAPSHOT_EXPIRED');return slot}
function closeSlot(slot:Slot){if(!slot.snapshot)return;clearTimeout(slot.snapshot.timer);try{slot.db.endSnapshot()}finally{slot.snapshot=undefined}}
function snapshot(args:any){const slot=free();slot.db.setControl(new Int32Array(args.control));slot.db.beginSnapshot({maxAgeMs:args.maxAgeMs});try{const revision=String(slot.db.query('SELECT revision FROM s0_identity WHERE id=1',[],{maxRows:1,maxBytes:256})[0]!.revision);const schema=slot.db.query('SELECT owner,version FROM s0_schema ORDER BY owner',[],{maxRows:10000,maxBytes:1048576}).map(row=>({owner:String(row.owner),version:Number(row.version)}));slot.snapshot={id:args.id,timer:setTimeout(()=>closeSlot(slot),args.maxAgeMs)};return {revision,schema}}catch(error){try{slot.db.endSnapshot()}catch{}throw error}}
function bounds(value:QueryBounds):QueryBounds{if(!Number.isSafeInteger(value.maxRows)||value.maxRows<1||value.maxRows>options.maxQueryRows||!Number.isSafeInteger(value.maxBytes)||value.maxBytes<1||value.maxBytes>options.maxQueryBytes)throw fault('SQLITE_QUERY_BUDGET');return value}
function close(){for(const slot of slots){closeSlot(slot);slot.db.close()}slots.length=0}
const operations=new Map<string,(args:any)=>unknown>([
 ['ready',()=>({ready:true})],
 ['snapshot',snapshot],
 ['snapshotQuery',(args:any)=>find(args.id).db.query(args.statement.sql,args.statement.parameters,bounds(args.bounds))],
 ['blob',(args:any)=>{if(args.request.maxBytes>options.maxQueryBytes)throw fault('SQLITE_QUERY_BUDGET');return find(args.id).db.readBlobRange(args.request)}],
 ['snapshotClose',(args:any)=>{const slot=slots.find(candidate=>candidate.snapshot?.id===args.id);if(slot)closeSlot(slot)}],
 ['state',()=>({slots:slots.length,open:slots.filter(slot=>slot.snapshot).length})],
 ['close',()=>{close();return true}],
]);
function dispatch(op:string,args:any):unknown{const operation=operations.get(op);if(!operation)throw fault('SQLITE_RPC_OPERATION',op);return operation(args)}
parentPort!.on('message',({id,op,args})=>{try{const value=dispatch(op,args);parentPort!.postMessage({id,value});if(op==='close')parentPort!.close()}catch(error){const e=error as Error&{code?:string};parentPort!.postMessage({id,error:{message:e.message,code:e.code??'SQLITE_ERROR'}})}});
