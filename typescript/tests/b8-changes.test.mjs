import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single,notices,silent} from './b8-support.mjs';
import {CHANGE_FILES_SQL,CHANGE_DOCS_SQL,CHANGE_ROWS_SQL} from '../.build/core/dist/public/index.js';

const file='/public/actors/actors.jsonl';
const changes=async(run,actor,from,to,request={maxItems:100,maxResponseBytes:1<<20})=>run.ctx.public.changes({actorId:actor,momentId:'m',query:null},String(from),String(to),request);
const one=async(run,actor,from,to)=> (await changes(run,actor,from,to)).items;

// B8：区间聚合以修订列为准，(R1,R2] 的增删改计数与路径正确。
test('(R1,R2] 聚合新增/更新/撤下与文件整体撤下',async()=>{
 const run=await setup({tables:[single,notices]});try{
  await run.runTx('b8-changes-r1',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A'},{id:'b',role:'all',name:'B'}]));
  const r1=await run.revision();
  await run.runTx('b8-changes-r2',tx=>run.ctx.public.writer(tx).upsert(single.path,[{id:'a',role:'all',name:'A2'},{id:'c',role:'all',name:'C'}]));
  const r2=await run.revision();
  await run.runTx('b8-changes-r3',tx=>run.ctx.public.writer(tx).retract(single.path,[{id:'b'}]));
  const r3=await run.revision();
  await run.runTx('b8-notice-r4',tx=>run.ctx.public.writer(tx).upsert(notices.path,[{id:'n1',text:'one'},{id:'n2',text:'two'}]));
  const r4=await run.revision();
  await run.runTx('b8-notice-r5',tx=>run.ctx.public.writer(tx).retract(notices.path,[{id:'n1'},{id:'n2'}]));
  const r5=await run.revision();

  const first=await one(run,'a',0,r1);
  assert.equal(first.length,1);
  assert.deepEqual({path:first[0].path,kind:first[0].kind,rows:first[0].rows},{path:file,kind:'added',rows:{added:2,updated:0,retracted:0}});

  const second=await one(run,'a',r1,r2);
  assert.equal(second.length,1,'同一文件的多次行变更按路径聚合成一条');
  assert.equal(second[0].path,file);
  assert.equal(second[0].kind,'updated');
  assert.deepEqual(second[0].rows,{added:1,updated:1,retracted:0});
  assert.equal(second[0].revision,String(r2));

  const third=await one(run,'a',r2,r3);
  assert.deepEqual({kind:third[0].kind,rows:third[0].rows},{kind:'updated',rows:{added:0,updated:0,retracted:1}});

  const removed=await one(run,'a',r4,r5);
  assert.equal(removed.length,1);
  assert.equal(removed[0].kind,'retracted');
  assert.deepEqual(removed[0].rows,{added:0,updated:0,retracted:2});

  const empty=await one(run,'a',r3,r3);
  assert.deepEqual(empty,[]);
 }finally{await run.close()}
});

// B8：受众可见性以文件受众与行受众求交，第三方受众看不到该文件。
test('受众过滤：不同角色只收到自己可见的行数',async()=>{
 const audienceProvider={id:'b8-roles',async audiences(scope){return scope.actorId==='supplier'?['all','role:supplier']:scope.actorId==='buyer'?['all','role:buyer']:['all']}};
 const run=await setup({tables:[single],audiences:audienceProvider});try{
  await run.runTx('b8-audience-r1',tx=>run.ctx.public.writer(tx).upsert(single.path,[
   {id:'s',role:'role:supplier',name:'供'},
   {id:'b',role:'role:buyer',name:'采'}],{audience:row=>row.role}));
  const r1=await run.revision();
  const supplier=await one(run,'supplier',0,r1);
  const buyer=await one(run,'buyer',0,r1);
  const outsider=await one(run,'outsider',0,r1);
  assert.equal(supplier.length,1);
  assert.deepEqual(supplier[0].rows,{added:1,updated:0,retracted:0},'供应商只应看到自己的行');
  assert.equal(buyer.length,1);
  assert.deepEqual(buyer[0].rows,{added:1,updated:0,retracted:0},'采购商只应看到自己的行');
  assert.deepEqual(outsider,[],'无交集受众看不到该文件的变更');
 }finally{await run.close()}
});

// B8：notify:'none' 仍保留在 Public.changes 中，并如实报告为 none。
test('notify none 的变更保留在变更日志并如实报告',async()=>{
 const run=await setup({tables:[single,silent]});try{
  await run.runTx('b8-none-r1',tx=>run.ctx.public.writer(tx).upsert(silent.path,[{id:'s',text:'静默'}]));
  const r1=await run.revision();
  const page=await changes(run,'a',0,r1);
  assert.equal(page.items.length,1);
  assert.equal(page.items[0].path,'/public/silent/silent.jsonl');
  assert.equal(page.items[0].notify,'none');
 }finally{await run.close()}
});

const sharded={path:'/public/bench',title:'基准',description:'基准分片',schema:{type:'object',properties:{id:{type:'string'},at:{type:'string'}}},key:['id'],sort:[{field:'id'}],shard:{by:'at',format:'value'}};
const rows=n=>Array.from({length:n},(_,i)=>({id:'r'+i,at:'file-'+i}));

async function dataset(files){
 const run=await setup({tables:[sharded]});
 await run.runTx('b8-scale-r1',tx=>run.ctx.public.writer(tx).upsert(sharded.path,rows(files)));
 const r1=await run.revision();
 await run.runTx('b8-scale-r2',tx=>run.ctx.public.writer(tx).upsert(sharded.path,rows(5).map((row,index)=>({...row,id:'changed-'+index}))));
 const r2=await run.revision();
 return {run,r1,r2};
}
const plan=async(run,sql,parameters=[])=>run.read(async view=>(await view.query({sql:'EXPLAIN QUERY PLAN '+sql,parameters},{maxRows:64,maxBytes:65536})).map(row=>String(row.detail)).join('\n'));

// B8：总量扩大 10 倍时，候选查询与行事件查询仍只走索引，读取行数不随总量增长。
test('总量×10时变更查询不扫描全表，计划与变更量一致',async()=>{
 const small=await dataset(100),large=await dataset(1000);
 try{
  const a=await small.run.ctx.public.changes({actorId:'a',momentId:'m',query:null},String(small.r1),String(small.r2),{maxItems:100,maxResponseBytes:1<<20});
  const b=await large.run.ctx.public.changes({actorId:'a',momentId:'m',query:null},String(large.r1),String(large.r2),{maxItems:100,maxResponseBytes:1<<20});
  assert.equal(a.items.length,5);
  assert.equal(b.items.length,5,'总量扩大后返回的变更数只由区间内变更决定');
  assert.ok(a.items.every(item=>item.kind==='updated'));
  assert.ok(b.items.every(item=>item.kind==='updated'));
  for(const sql of [CHANGE_FILES_SQL,CHANGE_DOCS_SQL,CHANGE_ROWS_SQL]){
   const parameters=sql===CHANGE_ROWS_SQL?['/public/bench/file-0.jsonl',0n,1n,'all','/public/bench/file-0.jsonl',0n,1n,'all']:[0n,1n,0n,1n];
   const smallPlan=await plan(small.run,sql,parameters),largePlan=await plan(large.run,sql,parameters);
   assert.equal(largePlan,smallPlan,'查询计划不应随总量变化');
   assert.doesNotMatch(smallPlan,/SCAN public_files/);
   assert.doesNotMatch(smallPlan,/SCAN public_rows/);
   assert.doesNotMatch(smallPlan,/SCAN public_nodes/);
   assert.match(smallPlan,/SEARCH public_(files|rows|nodes)/);
  }
  console.log(JSON.stringify({case:'B8-changes-scale',small:100,large:1000,items:a.items.length,changed:b.items.length}));
 }finally{await small.run.close();await large.run.close()}
});
