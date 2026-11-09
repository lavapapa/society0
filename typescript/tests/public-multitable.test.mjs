import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup} from './b4-support.mjs';

const dir='/public/econ';
const productsFile=dir+'/products.jsonl';
const recipesFile=dir+'/recipes.jsonl';
const page={maxItems:20,maxResponseBytes:1<<20};

const products={path:dir,title:'产品',description:'经济产品',schema:{type:'object',properties:{id:{type:'string'},text:{type:'string'}}},key:['id'],sort:[{field:'id'}],shard:{by:'',format:'none',file:()=> 'products'}};
const recipes={path:dir,title:'配方',description:'生产工艺',schema:{type:'object',properties:{id:{type:'string'},text:{type:'string'}}},key:['id'],sort:[{field:'id'}],shard:{by:'',format:'none',file:()=> 'recipes'}};
const directory={path:dir,title:'经济目录',description:'分片表',schema:{type:'object',properties:{id:{type:'string'},at:{type:'string'}}},key:['id'],sort:[{field:'id'}],shard:{by:'at',format:'month'}};

const text=bytes=>Buffer.from(bytes).toString('utf8');
const failure=code=>error=>error?.code===code||String(error).includes(code);

async function readText(run,path){
 return run.read(async view=>{
  const node=await run.ctx.public.mount.stat(path,run.scope('a','m'),view);
  const handle=await run.ctx.public.mount.open(node,run.scope('a','m'),view);
  try{
   const chunks=[];
   for await(const chunk of handle.value.read({offset:'0',length:Number(node.rawBytes),maxBytes:1<<24},new AbortController().signal))chunks.push(Buffer.from(chunk));
   return text(Buffer.concat(chunks));
  }finally{await handle.release()}
 });
}

test('同一目录两个文件 table 可共存，按完整文件路径读写',async()=>{
 const run=await setup({tables:[products,recipes]});try{
  await run.tx('multi-r1',async tx=>{
   await run.ctx.public.writer(tx).upsert(productsFile,[{id:'p1',text:'alpha 产品'}]);
   await run.ctx.public.writer(tx).upsert(recipesFile,[{id:'r1',text:'beta 配方'}]);
  });
  await run.openMoment('m');
  assert.equal(await readText(run,productsFile),'{"id":"p1","text":"alpha 产品"}\n');
  assert.equal(await readText(run,recipesFile),'{"id":"r1","text":"beta 配方"}\n');
 }finally{await run.close()}
});

test('同一目录两个文件 table 的可见性按文件受众分别生效',async()=>{
 const audiences={id:'multi-roles',async audiences(scope){return scope.actorId==='supplier'?['all','role:supplier']:scope.actorId==='buyer'?['all','role:buyer']:['all']}};
 const run=await setup({tables:[products,recipes],audiences});try{
  await run.tx('multi-vis-r1',async tx=>{
   const writer=run.ctx.public.writer(tx);
   await writer.upsert(productsFile,[{id:'p1',text:'alpha'}],{audience:'role:supplier'});
   await writer.upsert(recipesFile,[{id:'r1',text:'beta'}],{audience:'role:buyer'});
  });
  await run.openMoment('m');
  const session=await run.ctx.exploration.acquire(run.scope('supplier','m'),run.acquisition('supplier','m','multi-vis-supplier'));
  try{
   const listed=(await session.value.list(dir,page)).items.map(node=>node.path);
   assert.ok(listed.includes(productsFile),'供应商应看到产品文件');
   assert.ok(!listed.includes(recipesFile),'供应商不应看到配方文件');
   await assert.rejects(session.value.stat(recipesFile),failure('EXPLORATION_NOT_VISIBLE'));
  }finally{await session.release()}
  const buyer=await run.ctx.exploration.acquire(run.scope('buyer','m'),run.acquisition('buyer','m','multi-vis-buyer'));
  try{
   const listed=(await buyer.value.list(dir,page)).items.map(node=>node.path);
   assert.ok(listed.includes(recipesFile),'采购商应看到配方文件');
   assert.ok(!listed.includes(productsFile),'采购商不应看到产品文件');
   await assert.rejects(buyer.value.stat(productsFile),failure('EXPLORATION_NOT_VISIBLE'));
  }finally{await buyer.release()}
 }finally{await run.close()}
});

test('同一目录多个文件 table 的 ls/find/grep 各自列出和命中',async()=>{
 const run=await setup({tables:[products,recipes]});try{
  await run.tx('multi-explore-r1',async tx=>{
   const writer=run.ctx.public.writer(tx);
   await writer.upsert(productsFile,[{id:'p1',text:'needle 产品'}]);
   await writer.upsert(recipesFile,[{id:'r1',text:'needle 配方'}]);
  });
  await run.openMoment('m');
  const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','multi-explore'));
  try{
   const listed=(await session.value.list(dir,page)).items.map(node=>node.path);
   assert.ok(listed.includes(productsFile));
   assert.ok(listed.includes(recipesFile));
   assert.ok(listed.includes(dir+'/README.md'));
   const found=(await session.value.find({root:'/public',glob:'*.jsonl',page})).items.map(node=>node.path);
   assert.deepEqual(found,[productsFile,recipesFile]);
   const hits=await session.value.grep({root:'/public',pattern:'needle',mode:'literal',page});
   assert.deepEqual(hits.items.map(item=>item.path),[productsFile,recipesFile]);
   assert.equal(hits.total,'2');
  }finally{await session.release()}
 }finally{await run.close()}
});

test('同一目录多个文件 table 的 changes 分页按文件路径聚合',async()=>{
 const run=await setup({tables:[products,recipes]});try{
  await run.tx('multi-changes-r1',tx=>run.ctx.public.writer(tx).upsert(productsFile,[{id:'p1',text:'alpha'}]));
  await run.tx('multi-changes-r2',tx=>run.ctx.public.writer(tx).upsert(recipesFile,[{id:'r1',text:'beta'}]));
  const to=String(await run.read(view=>view.revision));
  const first=await run.ctx.public.changes(run.scope('a','m'),'0',to,{maxItems:1,maxResponseBytes:1<<20});
  assert.equal(first.total,'2');
  assert.equal(first.items.length,1);
  assert.ok(first.next);
  const second=await run.ctx.public.changes(run.scope('a','m'),'0',to,{maxItems:1,maxResponseBytes:1<<20,cursor:first.next});
  assert.equal(second.items.length,1);
  assert.equal(second.next,undefined);
  assert.deepEqual([first.items[0].path,second.items[0].path],[productsFile,recipesFile]);
 }finally{await run.close()}
});

test('同名文件路径仍报 PUBLIC_TABLE_EXISTS，目录 table 下的文件 table 报 PUBLIC_TABLE_CONFLICT',async()=>{
 const run=await setup({tables:[products]});try{
  assert.throws(()=>run.ctx.public.registry.table({...products}),failure('PUBLIC_TABLE_EXISTS'));
 }finally{await run.close()}
 const nested=await setup({tables:[directory]});try{
  assert.throws(()=>nested.ctx.public.registry.table(products),failure('PUBLIC_TABLE_CONFLICT'));
 }finally{await nested.close()}
 const reverse=await setup({tables:[products]});try{
  assert.throws(()=>reverse.ctx.public.registry.table(directory),failure('PUBLIC_TABLE_CONFLICT'));
 }finally{await reverse.close()}
});
