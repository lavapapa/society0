import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
const stage=fileURLToPath(new URL('../.build',import.meta.url));
const require=createRequire(stage+'/package.json');
test('official Philox4x32-10 known answer vectors',()=>{
 const {philox}=require('@society0/random-native');
 const text=readFileSync(new URL('../packages/random-native/vendor/random123-726a093cd9a73f3ec3c8d7a70ff10ed8efec8d13/tests/kat_vectors',import.meta.url),'utf8');
 let count=0;
 for(const line of text.split('\n')){
  const v=line.trim().split(/\s+/);if(v[0]!=='philox4x32'||v[1]!=='10')continue;
  const n=v.slice(2).map(x=>parseInt(x,16));assert.deepEqual(philox(n.slice(0,4),n.slice(4,6)),n.slice(6,10));count++;
 }
 assert.equal(count,3);
});
test('public Cordis random service is addressable',async()=>{
 const {createHost}=await import(pathToFileURL(require.resolve('@society0/host')));
 const {default:RandomStreams,Config,metadata,api}=await import(pathToFileURL(require.resolve('@society0/core/random')));
 assert.equal(RandomStreams.Config,Config);assert.equal(metadata.scope,'generation');assert.equal(api.provides[0].protocol,'random');
 const host=await createHost({boot:[]});const ctx=host.ctx;await ctx.plugin(RandomStreams,{algorithm:'philox4x32-10/v1',seed:'study-1'});
 let values;await ctx.plugin({inject:['random'],apply(ctx){values=Array.from({length:50},(_,i)=>ctx.random.at({run:'r',actor:'a',operation:'o',stream:'s',index:String(i)}));}});
 assert.equal(values.length,50);assert.ok(values.every(v=>Number.isInteger(v.uint32)&&v.uniform>=0&&v.uniform<1));await host.close();
});

const base={run:'研究-run',actor:'actor',operation:'sample',stream:'selection',index:'0'};
test('versioned UTF8 mapping agrees with independent C++ including uint64 boundaries',async()=>{
 const {at,encodeAddress}=require('@society0/random-native');
 const {execFileSync}=await import('node:child_process');
 const {mkdtempSync,rmSync}=await import('node:fs');
 const {tmpdir}=await import('node:os');
 const {dirname,join}=await import('node:path');
 const temporary=mkdtempSync(join(tmpdir(),'pr11-random-reference-'));
 const reference=join(temporary,'reference');
 const pkg=dirname(require.resolve('@society0/random-native/package.json'));
 execFileSync('g++',['-std=c++17','-O2','-I'+join(pkg,'vendor/random123-726a093cd9a73f3ec3c8d7a70ff10ed8efec8d13/include'),join(pkg,'source/reference.cc'),'-o',reference]);
 for(const index of ['0','1','4294967295','4294967296','9007199254740992','9007199254740993','18446744073709551615']){
  const a={...base,index,actor:'a\0😀',operation:'e\u0301',stream:''};
  const ref=JSON.parse(execFileSync(reference,['study-1',a.run,a.actor,a.operation,a.stream].map(x=>Buffer.from(x).toString('hex')).concat(index),{encoding:'utf8'}));
  assert.deepEqual([...encodeAddress('study-1',a)],ref.encoded);
  assert.deepEqual(at('study-1',a),{uint32:ref.uint32,uniform:ref.uniform});
  assert.equal(BigInt(ref.counter[0])+(BigInt(ref.counter[1])<<32n),BigInt(index));
 }
 assert.equal(dirname(temporary),resolve(tmpdir()));assert.ok(temporary.startsWith(join(tmpdir(),'pr11-random-reference-')));rmSync(temporary,{recursive:true});
});
test('no concatenation ambiguity and no implicit Unicode normalization',()=>{
 const {encodeAddress}=require('@society0/random-native');
 assert.notDeepEqual(encodeAddress('study-1',{...base,actor:'ab',operation:'c'}),encodeAddress('study-1',{...base,actor:'a',operation:'bc'}));
 assert.notDeepEqual(encodeAddress('study-1',{...base,actor:'é'}),encodeAddress('study-1',{...base,actor:'e\u0301'}));
});
test('invalid DTOs fail before native conversion; no unsafe-number truncation',()=>{
 const {at,philox}=require('@society0/random-native');
 for(const index of [0,9007199254740992,0n,-1,'-1','00','01','+1','1.0','1e3','',' 1','18446744073709551616','9'.repeat(100)])assert.throws(()=>at('study-1',{...base,index}),/RANDOM_INDEX/);
 for(const field of ['run','actor','operation','stream'])for(const value of [null,undefined,1,'\ud800'])assert.throws(()=>at('study-1',{...base,[field]:value}),/RANDOM_ADDRESS/);
 assert.throws(()=>at('\ud800',base),/RANDOM_ADDRESS/);
 assert.throws(()=>philox([-1,0,0,0],[0,0]),/RANDOM_UINT32/);
 assert.throws(()=>philox([0,0,0],[0,0]),/RANDOM_ARRAY_LENGTH/);
});
test('reverse calls, async concurrency, unrelated draws and segmented recovery agree',async()=>{
 const {createHost}=await import(pathToFileURL(require.resolve('@society0/host')));
 const {default:RandomStreams}=await import(pathToFileURL(require.resolve('@society0/core/random')));
 async function open(){const h=await createHost({boot:[]});await h.ctx.plugin(RandomStreams,{algorithm:'philox4x32-10/v1',seed:'study-1'});return h;}
 const h=await open();const addresses=Array.from({length:100},(_,i)=>({...base,index:String(9007199254740990n+BigInt(i))}));
 const expected=addresses.map(a=>h.ctx.random.at(a));
 for(let i=0;i<100;i++)h.ctx.random.at({...base,run:'unrelated',index:String(i)});
 assert.deepEqual(addresses.toReversed().map(a=>h.ctx.random.at(a)).toReversed(),expected);
 assert.deepEqual(await Promise.all(addresses.map(async a=>{await Promise.resolve();return h.ctx.random.at(a)})),expected);
 const first=addresses.slice(0,37).map(a=>h.ctx.random.at(a));await h.close();
 const restored=await open();assert.deepEqual([...first,...addresses.slice(37).map(a=>restored.ctx.random.at(a))],expected);await restored.close();
});
test('separate workers have identical addresses without global draw state',async()=>{
 const {Worker}=await import('node:worker_threads');const {at}=require('@society0/random-native');
 const url=pathToFileURL(require.resolve('@society0/random-native')).href;
 const code=`import {parentPort,workerData} from 'node:worker_threads';import {at} from ${JSON.stringify(url)};parentPort.postMessage(workerData.map(a=>at('study-1',a)));`;
 const a=Array.from({length:64},(_,i)=>({...base,index:String(i)}));
 const run=addresses=>new Promise((resolve,reject)=>{const w=new Worker(new URL('data:text/javascript,'+encodeURIComponent(code)),{workerData:addresses});let value;w.on('message',v=>value=v);w.on('error',reject);w.on('exit',c=>c?reject(Error('worker exit '+c)):resolve(value));});
 const [forward,reverse]=await Promise.all([run(a),run(a.toReversed())]);assert.deepEqual(forward,reverse.toReversed());assert.deepEqual(forward,a.map(x=>at('study-1',x)));
});

test('frozen version-one address regression vectors',()=>{
 const {at,encodeAddress,philox}=require('@society0/random-native');
 const rows=JSON.parse(readFileSync(new URL('../packages/random-native/source/address-vectors.json',import.meta.url),'utf8'));
 for(const {seed,address,expected} of rows){
  assert.deepEqual([...encodeAddress(seed,address)],expected.encoded);
  assert.deepEqual(at(seed,address),{uint32:expected.uint32,uniform:expected.uniform});
  assert.equal(philox(expected.counter,expected.key)[0],expected.uint32);
 }
});
