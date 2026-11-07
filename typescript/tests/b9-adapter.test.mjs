import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup} from './b4-support.mjs';
import {Information} from '../.build/core/dist/information/index.js';
import {publicFromInformation} from '../.build/core/dist/information/adapter.js';

const rows=[
 {id:'r1',at:'2026-11-01',text:'第一条'},
 {id:'r2',at:'2026-11-01',text:'第二条'},
 {id:'r3',at:'2026-11-02',text:'第三条'},
 {id:'r4',at:'2026-11-02',text:'第四条'},
 {id:'r5',at:'2026-11-03',text:'第五条'},
];

function source(){
 return {
  id:'legacy.b9',schema:{type:'object'},capabilities:['page'],
  async available(){return true},
  async query(input,scope,view,request){
   const start=request.cursor===undefined?0:Number(request.cursor);
   const size=Math.min(request.maxItems,2),items=rows.slice(start,start+size).map(value=>({kind:'inline',value}));
   const next=start+items.length<rows.length?String(start+items.length):undefined;
   return {items,total:String(rows.length),...(next?{next}:{}),revision:view.revision,responseBytes:0};
  }
 };
}

async function full(source,scope,view){
 const values=[];let cursor;
 do{
  const result=await source.query({},scope,view,{maxItems:2,maxResponseBytes:1<<20,...(cursor?{cursor}:{})});
  values.push(...result.items.map(item=>item.value));
  cursor=result.next;
 }while(cursor);
 return values;
}

test('B9 information适配器：publicFromInformation渲染与query({})全量分页一致',async()=>{
 const run=await setup({tables:[]});let lease;
 try{
  await run.host.ctx.plugin(Information,{});
  const legacy=source();
  run.host.ctx.information.register(legacy);
  const adapter=publicFromInformation(legacy,{path:'/public/legacy',shard:{by:'at',format:'day'}});
  assert.equal(adapter.path,'/public/legacy');
  const scope={actorId:'a',momentId:'m',query:null};
  const rendered=await run.read(async view=>{
   const versions=await adapter.versions(scope,view);
   assert.deepEqual(versions,[{owner:'information/legacy.b9',version:String(view.revision)}]);
   const listed=await adapter.list(adapter.path,scope,view);
   assert.deepEqual(listed.map(item=>item.name),['2026-11-01.jsonl','2026-11-02.jsonl','2026-11-03.jsonl']);
   const lines=[];
   for(const item of listed){
    const path=adapter.path+'/'+item.name;
    for await(const chunk of adapter.render(path,scope,view,new AbortController().signal))lines.push(Buffer.from(chunk).toString('utf8').trimEnd());
   }
   return lines;
  });
  const expected=await run.read(view=>full(legacy,scope,view));
  assert.deepEqual(rendered,expected.map(value=>JSON.stringify(value)),'适配器逐行渲染必须与query({})全量分页一致');
 }finally{
  try{await lease?.release()}catch{}
  await run.close();
 }
});
