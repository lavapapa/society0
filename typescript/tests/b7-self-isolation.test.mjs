import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,inventoryProvider,secretProvider,nestedSecretProvider,acquire,readFile,act} from './b7-support.mjs';

const page={maxItems:100,maxResponseBytes:1<<20};

// B7：/self 是 actor 命名空间。A 只能看到自己的内容；B 专属路径对 A 不可见
// （列表里没有该路径、stat 返回 NOT_VISIBLE，而不是“可见但拒绝读取”）；
// 只含 B 可见提供者的目录及其父目录也必须对 A 不可见。
test('actor A看不到B的/self，B专属路径与目录对A不可见',async()=>{
 const run=await setup();let a,b;
 try{
  run.host.ctx.publicSelf.provide(inventoryProvider());
  run.host.ctx.publicSelf.provide(secretProvider());
  run.host.ctx.publicSelf.provide(nestedSecretProvider());
  await act(run,{actorId:'A',id:'a-only',qty:1});
  await act(run,{actorId:'B',id:'b-only',qty:2});
  await run.openMoment('m','b7-iso-open');

  a=await acquire(run,'A','m','b7-iso-a');
  b=await acquire(run,'B','m','b7-iso-b');

  const aView=await readFile(a.value,'/self/inventory.jsonl');
  assert.equal(aView.text,'{"id":"a-only","qty":1}\n','A 只能读到自己的行');
  const bView=await readFile(b.value,'/self/inventory.jsonl');
  assert.equal(bView.text,'{"id":"b-only","qty":2}\n','B 只能读到自己的行');

  await assert.rejects(()=>a.value.stat('/self/secret.jsonl'),error=>error.code==='EXPLORATION_NOT_VISIBLE','B 专属路径对 A 必须不可见');
  const listed=await a.value.list('/self',page);
  const paths=listed.items.map(node=>node.path);
  assert.ok(paths.includes('/self/inventory.jsonl'));
  assert.ok(paths.includes('/self/README.md'));
  assert.equal(paths.includes('/self/secret.jsonl'),false,'B 专属路径不应出现在 A 的列表中');
  assert.equal(paths.includes('/self/contracts'),false,'只含 B 可见项的目录不应出现在 A 的列表中');

  await assert.rejects(()=>a.value.stat('/self/contracts/with-b'),error=>error.code==='EXPLORATION_NOT_VISIBLE','B 专属嵌套目录对 A 必须不可见');
  await assert.rejects(()=>a.value.stat('/self/contracts'),error=>error.code==='EXPLORATION_NOT_VISIBLE','父目录不含 A 可见项时也必须不可见');
  await assert.rejects(()=>a.value.list('/self/contracts',page),error=>error.code==='EXPLORATION_NOT_VISIBLE');

  const bSecret=await readFile(b.value,'/self/secret.jsonl');
  assert.equal(bSecret.text,'secret:B\n');
  const bNested=await readFile(b.value,'/self/contracts/with-b/secret.jsonl');
  assert.equal(bNested.text,'nested:B\n');
  const bDirectory=await b.value.stat('/self/contracts');
  assert.equal(bDirectory.kind,'directory');
  const bRoot=await b.value.list('/self',page);
  assert.ok(bRoot.items.map(node=>node.path).includes('/self/contracts'),'B 应能看到自己的目录');

  const bNode=await b.value.stat('/self/inventory.jsonl');
  await assert.rejects(()=>a.value.open(bNode),error=>error.code==='EXPLORATION_STALE','A 不能复用 B 的节点读取 B 的内容');
 }finally{
  try{await a?.release()}catch{}
  try{await b?.release()}catch{}
  await run.close();
 }
});
