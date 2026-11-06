import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readRows,renderRows} from '../.build/core/dist/public/store.js';
import {setup,single} from './b2-support.mjs';

const file='/public/actors/actors.jsonl';
let seed=20261106;
const random=()=>{seed=(seed*1103515245+12345)%2147483648;return seed/2147483648};
test('随机500行、3个受众：渲染字节等于存储行、解析等于可见集合',async()=>{
 const run=await setup();try{
  const rows=[];
  for(let i=0;i<500;i++)rows.push({id:'a'+i,role:['all','role:supplier','role:buyer'][i%3],name:'名称'+Math.floor(random()*10000)});
  const result=await run.tx('b2-roundtrip',tx=>run.ctx.public.writer(tx).upsert(single.path,rows,{audience:row=>row.role}));
  const audiences=['all','role:supplier'];
  await run.read(async view=>{
   const stored=await readRows(view,file,result.receipt.revision,audiences,{maxRows:1000,maxBytes:1<<22});
   const visible=rows.filter(row=>audiences.includes(row.role)).sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:a.id<b.id?-1:1);
   assert.equal(stored.length,visible.length);
   const expected=visible.map(row=>JSON.stringify({id:row.id,role:row.role,name:row.name})+'\n').join('');
   const rendered=Buffer.from(await renderRows(view,file,result.receipt.revision,audiences)).toString('utf8');
   assert.equal(rendered,expected);
   assert.equal(Buffer.concat(stored.flatMap(row=>[Buffer.from(row.line),Buffer.from('\n')])).toString('utf8'),expected);
   assert.deepEqual(stored.map(row=>JSON.parse(Buffer.from(row.line).toString('utf8'))),visible.map(row=>({id:row.id,role:row.role,name:row.name})));
  });
 }finally{await run.close()}
});
