import test from 'node:test';
import assert from 'node:assert/strict';
import {setup} from './pr07-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ContextMount} from '../.build/core/dist/mount-context/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';
import {formatToolResult} from '../.build/core/dist/actor-tools/format.js';

async function harness(t,config={}){
  const run=await setup({migrations:[actorToolsMigration({test:'d2-tool-surface'})]});
  t.after(()=>run.close());
  for(const plugin of [Prompts,ContextMount,ExplorationShell,ActorTools])await run.host.ctx.plugin(plugin,config);
  let ctx;await run.host.ctx.plugin({inject:['actorTools'],apply(c){ctx=c}});
  return {run,ctx};
}
const tool=(binding,name)=>{const found=binding.value.tools.find(item=>item.name===name);assert.ok(found,'tool '+name);return found};
test('D2 default tool surface is exactly act/bash/find/grep/read and identical across actors and moments',async t=>{
  const {run,ctx}=await harness(t);
  const first=await ctx.actorTools.bind(run.context('d2-one','a','m1'));
  const second=await ctx.actorTools.bind(run.context('d2-two','b','m2'));
  try{
    const names=first.value.tools.map(item=>item.name).sort();
    assert.deepEqual(names,['act','bash','find','grep','read']);
    assert.equal(JSON.stringify(first.value.tools),JSON.stringify(second.value.tools));
  }finally{await first.release();await second.release()}
});
test('D2 legacyTools information restores information tools only',async t=>{
  const {run,ctx}=await harness(t,{legacyTools:['information']});
  const binding=await ctx.actorTools.bind(run.context('d2-legacy'));
  try{
    const names=binding.value.tools.map(item=>item.name);
    assert.ok(names.includes('information_discover'));
    assert.ok(names.includes('information_query'));
    assert.equal(names.some(name=>name.startsWith('exploration_')),false);
    assert.ok(names.includes('act'));
  }finally{await binding.release()}
});
test('D2 read with only path returns a continuation hint',async t=>{
  const {run,ctx}=await harness(t,{maxReadBytes:8});
  const context=run.context('d2-read');
  const binding=await ctx.actorTools.bind(context);
  try{
    await tool(binding,'bash').execute('d2-write',{command:"printf '0123456789abcdef' > /workspace/d2-read.txt"},context.signal);
    const output=await tool(binding,'read').execute('d2-read',{path:'/workspace/d2-read.txt'},context.signal);
    const text=output.content[0].text;
    assert.match(text,/^# \/workspace\/d2-read\.txt /);
    assert.match(text,/01234567/);
    assert.doesNotMatch(text,/89abcdef/);
    assert.match(text,/# 续读: read path=\/workspace\/d2-read\.txt offset=8/);
  }finally{await binding.release()}
});
test('D2 grep prints GNU path:line:text and no match stays completed',async t=>{
  const {run,ctx}=await harness(t);
  const context=run.context('d2-grep');
  const binding=await ctx.actorTools.bind(context);
  try{
    await tool(binding,'bash').execute('d2-write',{command:"printf 'alpha\\nneedle here\\n' > /workspace/d2-grep.txt"},context.signal);
    const hit=await tool(binding,'grep').execute('d2-hit',{pattern:'needle',root:'/workspace'},context.signal);
    assert.match(hit.content[0].text,/\/workspace\/d2-grep\.txt:2:needle/);
    const miss=await tool(binding,'grep').execute('d2-miss',{pattern:'absent-token',root:'/workspace'},context.signal);
    assert.equal(miss.details.status,'completed');
    assert.match(miss.content[0].text,/# 共 0 项/);
  }finally{await binding.release()}
});
test('D2 receipts keep the structured original value',async t=>{
  const {run,ctx}=await harness(t);
  const context=run.context('d2-receipt');
  const binding=await ctx.actorTools.bind(context);
  try{
    await tool(binding,'bash').execute('d2-write',{command:"printf 'structured' > /workspace/d2-receipt.txt"},context.signal);
    await tool(binding,'read').execute('d2-read',{path:'/workspace/d2-receipt.txt'},context.signal);
    const facts=await binding.value.actionReceipts.read();
    const read=facts.find(item=>item.action==='read');
    assert.ok(read);
    assert.equal(read.receipt.status,'completed');
    assert.equal(read.receipt.value.node.path,'/workspace/d2-receipt.txt');
    assert.equal(read.receipt.value.text,'structured');
  }finally{await binding.release()}
});
test('D2 formatter renders errors and find pages as text',()=>{
  assert.equal(formatToolResult('find',{status:'completed',value:{items:[{path:'/workspace/a',kind:'file'},{path:'/workspace/d',kind:'directory'}],total:'2'}}),'/workspace/a\n/workspace/d/\n# 共 2 项');
  assert.equal(formatToolResult('read',{status:'rejected',value:{code:'ACTION_NOT_OFFERED',action:'bash'}}),'错误 ACTION_NOT_OFFERED：bash');
});
