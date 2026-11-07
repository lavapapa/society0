import assert from 'node:assert/strict';
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
import {createPreflightSession} from '@society0/host';
const root=new URL('./node_modules/@fixture/',import.meta.url);
function fixture(name, manifest) {const dir=new URL(name+'/',root);fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(new URL('package.json',dir),JSON.stringify({name:'@fixture/'+name,version:'1.0.0',exports:{'./a':'./a.js','./b':'./b.js'},...manifest}));fs.writeFileSync(new URL('a.js',dir),'export default {}');fs.writeFileSync(new URL('b.js',dir),'export default {}');}
fixture('leaf',{});
fixture('root',{peerDependencies:{'@society0/host':'^0.1.0'},dependencies:{'@fixture/leaf':'1.0.0'}});
fixture('bad',{peerDependencies:{'@society0/host':'invalid'}});
let reads=0;const original=fs.readFileSync;
fs.readFileSync=function(path,...args){if(String(path).endsWith('@fixture/leaf/package.json'))reads++;return original.call(this,path,...args)};syncBuiltinESMExports();
try {
 const session=createPreflightSession();
 assert.equal(session.preflight(['@fixture/root/a'],import.meta.url).length,1);
 const first=reads;assert.ok(first>0);
 assert.equal(session.preflight(['@fixture/root/b'],import.meta.url).length,1);
 assert.equal(reads,first,'same batch reuses validated dependency graph');
 createPreflightSession().preflight(['@fixture/root/a'],import.meta.url);
 assert.ok(reads>first,'new batch inspects dependencies again');
 for(let i=0;i<2;i++)assert.throws(()=>session.preflight(['@fixture/bad/a'],import.meta.url),/PEER_CONFLICT/,'failed traversal must not poison retry');
 fixture('leaf',{peerDependencies:{'@society0/host':'invalid'}});
 assert.throws(()=>createPreflightSession().preflight(['@fixture/root/a'],import.meta.url),/PEER_CONFLICT/,'new batch observes changed dependency manifest');
 console.log('batch graph reuse, fresh batch validation, failed retry isolation: passed');
} finally {fs.readFileSync=original;syncBuiltinESMExports();}
