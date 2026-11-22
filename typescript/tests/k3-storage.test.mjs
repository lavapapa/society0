import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,readdir,readFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {LocalArtifacts} from '../.build/core/dist/artifacts-local/store.js';
import {syncStats} from '../.build/core/dist/artifacts-local/files.js';
import {fixture,prepare,advance,publish,targetOf} from './pr05-support.mjs';
async function* bytes(text){yield Buffer.from(text)}
async function collect(iterable){const parts=[];for await(const chunk of iterable)parts.push(chunk);return Buffer.concat(parts).toString()}

test('K3-S2 去重、封存两次 fsync、重开后按 pack 读回、未封存对象重开即丢',async()=>{
 const dir=join(await mkdtemp(join(tmpdir(),'k3-store-')),'a');
 const store=await LocalArtifacts.open({directory:dir});
 const a=await store.stage(bytes('alpha'),'text/plain'),again=await store.stage(bytes('alpha'),'text/plain'),b=await store.stage(bytes('beta'),'text/plain');
 assert.equal(a.id,again.id);assert.equal(store.diagnostics().staged,2);
 const syncs=syncStats.calls,pack=await store.seal([a,b]);
 assert.equal(syncStats.calls-syncs,1,'封存只 fsync pack 文件和 packs 目录各一次，syncStats 只计目录那次');
 assert.deepEqual(await readdir(join(dir,'staging')),[]);
 const lost=await store.stage(bytes('gamma'),'text/plain');
 const reopened=await LocalArtifacts.open({directory:dir});
 await reopened.load([{path:pack.path,bytes:pack.bytes}],'/');
 assert.equal(await collect(reopened.read(a)),'alpha');assert.equal(await collect(reopened.read(b,{offset:'1',length:2})),'et');
 await assert.rejects(reopened.inspect(lost.id),{code:'ARTIFACT_MISSING'});
 assert.equal(await readFile((await reopened.localFile(b)).path,'utf8'),'beta');
});

test('K3-S3 分叉不复制对象：子点引用父 pack，写 sessions/parent.json 和分阶段计时',async()=>{
 const f=await fixture({bodyChunks:2});
 try{
  const root=await prepare(f);assert.equal(root.packs.length,1);assert.deepEqual(root.sessions,{});
  await advance(f,1);const {point,lease}=await publish(f,root);f.fence.resume(lease);
  assert.deepEqual(point.packs,root.packs,'没有新对象就不封新 pack');
  const target=targetOf(f),child=await f.history.fork({operationId:'k3-fork',pointId:point.id,targetRunId:'child',targetDirectory:target});
  assert.deepEqual(child.lineage,{run:'../'+f.directory.split('/').pop(),pointId:point.id});
  assert.ok(child.packs[0].path.startsWith('../'));
  assert.deepEqual(await readdir(join(target,'artifacts')).catch(()=>[]),[],'子目录没有对象副本');
  assert.deepEqual(JSON.parse(await readFile(join(target,'sessions','parent.json'),'utf8')),{run:child.lineage.run,heads:point.sessions});
  const metric=JSON.parse((await readFile(join(target,'metrics.jsonl'),'utf8')).trim());
  assert.equal(metric.type,'fork');assert.deepEqual(Object.keys(metric.phases),['materialize','prepare','root','descriptor']);
  const probe=await LocalArtifacts.open({directory:join(target,'artifacts')});await probe.load(child.packs,target);
  assert.equal((await probe.inspect(f.body.id)).bytes,'131072');
 }finally{await f.host.close()}
});
