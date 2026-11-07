import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup,single} from './b4-support.mjs';

const file='/public/actors/actors.jsonl';
const rows=[
 {id:'a',role:'all',name:'needle alpha'},
 {id:'b',role:'all',name:'NEEDLE beta 中文'},
 {id:'c',role:'all',name:'锂价 2026'},
 {id:'d',role:'all',name:'noodle'}
];
const page={maxItems:100,maxResponseBytes:1<<20};
const text=bytes=>Buffer.from(bytes).toString('utf8');

function reference(lines,pattern,mode,ignoreCase=false){
 const out=[];
 const expression=mode==='regex'?new RegExp(pattern,ignoreCase?'gi':'g'):undefined;
 lines.forEach((line,index)=>{
  if(mode==='regex'){
   for(const match of line.matchAll(expression))out.push({path:file,line:index+1,column:Buffer.byteLength(line.slice(0,match.index))+1,lineText:line});
  }else{
   const source=ignoreCase?line.toLowerCase():line,target=ignoreCase?pattern.toLowerCase():pattern;
   let offset=0;
   while(true){const at=source.indexOf(target,offset);if(at<0)break;out.push({path:file,line:index+1,column:Buffer.byteLength(line.slice(0,at))+1,lineText:line});offset=at+Math.max(1,target.length)}
  }
 });
 return out;
}

test('grep与完整原文逐条一致：字面量、正则、-i、中文',async()=>{
 const run=await setup();try{
  await run.tx('b5-exact-seed',tx=>run.ctx.public.writer(tx).upsert(single.path,rows));
  await run.openMoment('m');
  const session=await run.ctx.exploration.acquire(run.scope('a','m'),run.acquisition('a','m','b5-exact'));
  try{
   let lines;
   await run.read(async view=>{
    const node=await run.ctx.public.mount.stat(file,run.scope('a','m'),view);
    const handle=await run.ctx.public.mount.open(node,run.scope('a','m'),view);
    try{const chunks=[];for await(const chunk of handle.value.read({offset:'0',length:Number(node.rawBytes),maxBytes:1<<24},new AbortController().signal))chunks.push(Buffer.from(chunk));lines=text(Buffer.concat(chunks)).trimEnd().split('\n')}finally{await handle.release()}
   });
   for(const request of [
    {pattern:'needle',mode:'literal'},
    {pattern:'n[e]+dle',mode:'regex'},
    {pattern:'NEEDLE',mode:'literal',ignoreCase:true},
    {pattern:'锂价',mode:'literal'}
   ]){
    const result=await session.value.grep({root:'/public',...request,page});
    const actual=result.items.map(item=>({path:item.path,line:item.line,column:item.column,lineText:item.lineText}));
    const expected=reference(lines,request.pattern,request.mode,request.ignoreCase);
    assert.deepEqual(actual,expected,`${request.pattern} (${request.mode})`);
   }
  }finally{await session.release()}
 }finally{await run.close()}
});
