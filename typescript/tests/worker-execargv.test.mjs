import test from 'node:test';import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';
const exec=promisify(execFile),core=new URL('../.build/',import.meta.url).href,root=new URL('..',import.meta.url);
const bootstrap=`const {mkdtemp}=await import('node:fs/promises');const {tmpdir}=await import('node:os');const {join}=await import('node:path');`;
const cases={
 'sqlite rpc':`${bootstrap}const {Database}=await import('${core}core/dist/sqlite/database.js');const {ExecutionGate}=await import('${core}core/dist/execution-fence/state.js');
const directory=await mkdtemp(join(tmpdir(),'rv311-rpc-')),gate=new ExecutionGate({runId:'rv3-11',generation:'g1',maxAcceptedCommits:20,readRevision:()=>'0'});
const db=await Database.open({directory,runId:'rv3-11',maxTransactionMs:20000},gate);
try{await db.applyMigrations([{owner:'notes',from:0,to:1,after:[],source:{package:'notes',version:'1'},statements:[{sql:'CREATE TABLE notes(id INTEGER PRIMARY KEY,body TEXT NOT NULL)'}]}]);await db.transaction(await gate.reserve({id:'rv3-11'}).then(item=>item.lease),{operationId:'rv3-11',maxDurationMs:15000,maxStatements:4,maxInputBytes:65536,maxChangedRows:10,maxCapturedBytes:65536},async tx=>{await tx.execute({sql:'INSERT INTO notes VALUES (?,?)',parameters:[1n,'x']})})}finally{await db.close()}`,
 'attempts ledger':`${bootstrap}const {AttemptLedger}=await import('${core}core/dist/attempts/ledger.js');const {LocalArtifacts}=await import('${core}core/dist/artifacts-local/store.js');
const directory=await mkdtemp(join(tmpdir(),'rv311-attempts-')),objects=await LocalArtifacts.open({directory:join(directory,'artifacts'),chunkBytes:65536});
const ledger=await AttemptLedger.open({directory:join(directory,'lineage'),lineage:'lineage-1'},objects);
try{await ledger.initialize()}finally{await ledger.close()}`,
 'history io':`${bootstrap}const {openConnection}=await import('${core}sqlite-native/src/index.mjs');const {nativeJob}=await import('${core}core/dist/sqlite-history/io.js');
const directory=await mkdtemp(join(tmpdir(),'rv311-io-')),path=join(directory,'source.db'),target=join(directory,'backup.db');
const db=openConnection({path});db.execSchema('CREATE TABLE t(id INTEGER PRIMARY KEY,body TEXT)');db.begin();db.execute('INSERT INTO t VALUES (1,?)',['x']);db.commit();db.close();
const {result}=await nativeJob({operation:'backup',path,target,maxBytes:16777216});
if(!result||typeof result.bytes!=='number')throw Error('HISTORY_BACKUP_RESULT')`,
};
for(const [name,code] of Object.entries(cases))test('RV3-11-T01 '+name+' Worker starts under --input-type=module -e',async()=>{
 const result=await exec(process.execPath,['--input-type=module','-e',code],{cwd:root,timeout:120000}).catch(error=>error);
 assert.equal(result.code??0,0,String(result.stderr??result));
});
