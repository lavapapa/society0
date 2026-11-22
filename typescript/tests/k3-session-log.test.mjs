import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,stat,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {SessionLog,sessionFile,packText} from '../.build/core/dist/session-log/index.js';
import {Replay} from '../.build/core/dist/replay/index.js';
import {setup} from './pr07-support.mjs';

const key={actorId:'a',momentId:'m'};
const model={id:'m1',provider:'p'},options={maxTokens:64},tools=[{name:'act'}];
const message=(role,index)=>({role,content:role+'-'+index+'-'+'x'.repeat(8000)});
async function events(log,k=key){const out=[];for await(const event of log.read(k))out.push(event);return out}
const size=async(directory,k=key)=>(await stat(join(directory,sessionFile(k)))).size;

test('K3 每轮写入字节约等于本轮新增消息，header 只写一次',async()=>{
  const directory=join(await mkdtemp(join(tmpdir(),'k3-session-')),'sessions'),log=new SessionLog(directory,5);
  const messages=[];let previous=0;const deltas=[];
  for(let round=0;round<6;round++){
    const added=[message('user',round),message('assistant',round)];messages.push(...added);
    log.request(key,{model,context:{systemPrompt:'sys',messages:[...messages],tools},options},{operationId:'op/'+round,attempt:0});
    log.append(key,'response',{operationId:'op/'+round,attempt:0,message:{role:'assistant'}});
    await log.barrier();
    const now=await size(directory);deltas.push({delta:now-previous,added:Buffer.byteLength(JSON.stringify(added))});previous=now;
  }
  for(const {delta,added} of deltas.slice(1)){assert.ok(delta>=added,JSON.stringify({delta,added}));assert.ok(delta-added<600,JSON.stringify({delta,added}))}
  const all=await events(log);
  assert.equal(all.filter(event=>event.type==='request/header').length,1);
  assert.deepEqual(all.filter(event=>event.type.startsWith('context/')).map(event=>event.type),['context/reset',...Array(5).fill('context/append')]);
  assert.deepEqual(all.map(event=>event.seq),all.map((_,index)=>index));
  assert.deepEqual(all.filter(event=>event.type==='request').map(event=>[event.data.contextLen,event.data.headerSeq]),[2,4,6,8,10,12].map(length=>[length,0]));
  // 上下文不是延续（被压缩）时写全量
  log.request(key,{model,context:{systemPrompt:'sys',messages:[message('user','summary')],tools},options},{operationId:'op/reset',attempt:0});
  assert.equal((await events(log)).at(-2).type,'context/reset');
  await log.close();
  console.log(JSON.stringify({case:'K3-01',deltas}));
});

test('K3 heads/truncate 截回点上长度，删除点后新建的文件，seq 接续',async()=>{
  const directory=join(await mkdtemp(join(tmpdir(),'k3-session-')),'sessions'),log=new SessionLog(directory,5),other={actorId:'b',momentId:'m'};
  log.append(key,'action',{operationId:'x',attempt:0});
  const heads=await log.heads();
  assert.deepEqual(Object.keys(heads),[sessionFile(key)]);
  log.append(key,'action',{operationId:'y',attempt:0});log.append(other,'action',{operationId:'z',attempt:0});
  await log.barrier();
  assert.ok(await size(directory)>heads[sessionFile(key)]);
  await log.truncate(heads);
  assert.equal(await size(directory),heads[sessionFile(key)]);
  await assert.rejects(stat(join(directory,sessionFile(other))),{code:'ENOENT'});
  assert.deepEqual((await events(log)).map(event=>event.data.operationId),['x']);
  log.append(key,'action',{operationId:'w',attempt:0});
  assert.deepEqual((await events(log)).map(event=>[event.seq,event.data.operationId]),[[0,'x'],[1,'w']]);
  assert.equal((await log.find(key,{operationId:'w',attempt:0})).type,'action');
  assert.equal(await log.find(key,{operationId:'y',attempt:0}),undefined);
  await log.close();
});

test('K3 分叉世界经 parent.json 读父前缀，父世界后续写入不可见',async()=>{
  const base=await mkdtemp(join(tmpdir(),'k3-session-')),parent=new SessionLog(join(base,'src','sessions'),5);
  parent.append(key,'action',{operationId:'p1',attempt:0});
  const heads=await parent.heads();
  parent.append(key,'action',{operationId:'p2',attempt:0});await parent.close();
  await mkdir(join(base,'dst','sessions'),{recursive:true});
  await writeFile(join(base,'dst','sessions','parent.json'),JSON.stringify({run:'../src',heads}));
  const child=new SessionLog(join(base,'dst','sessions'),5);
  child.append(key,'action',{operationId:'c1',attempt:0});
  assert.deepEqual((await events(child)).map(event=>[event.seq,event.data.operationId]),[[0,'p1'],[1,'c1']]);
  const own=(await readFile(join(base,'dst','sessions',sessionFile(key)),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
  assert.deepEqual(own.map(line=>line.type),['session','action']);
  assert.equal((await child.find(key,{operationId:'p1',attempt:0})).type,'action');
  assert.equal(await child.find(key,{operationId:'p2',attempt:0}),undefined);
  await child.close();
});

test('K3 replay.lookup 从 session 日志读结论事件，缺失即 REPLAY_MISSING',async()=>{
  const run=await setup();
  try{
    await run.host.ctx.plugin(Replay,{});
    let replay;await run.host.ctx.plugin({inject:['replay'],apply(ctx){replay=ctx.replay}});
    const raw=packText('{"vendor":"opaque"}');
    run.ctx.sessions.request(key,{model,context:{messages:[message('user',0)]},options},{operationId:'model/1',attempt:0});
    run.ctx.sessions.append(key,'attempt/failed',{operationId:'model/1',attempt:0,cause:'MODEL_UPSTREAM_ERROR'});
    run.ctx.sessions.append(key,'response',{operationId:'model/1',attempt:1,message:{role:'assistant'},raw});
    const failed=await replay.lookup({session:key,operationId:'model/1',attempt:0});
    assert.equal(failed.type,'attempt/failed');
    const found=await replay.lookup({session:key,operationId:'model/1',attempt:1});
    assert.equal(found.type,'response');
    assert.equal(replay.raw(found.data.raw),'{"vendor":"opaque"}');
    await assert.rejects(replay.lookup({session:key,operationId:'model/2',attempt:0}),/REPLAY_MISSING model\/2/);
  }finally{await run.host.close()}
});
