import {test} from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {readdir} from 'node:fs/promises';
import {socialRun,context,openMoment,readText,grep,parseLines} from './b9-support.mjs';
import {setup,frontier} from './b4-support.mjs';
import {openConnection} from '../.build/sqlite-native/src/index.mjs';

const day='1762387200';
const postFile='/public/social/posts/'+day+'.jsonl';
const profileFile='/public/social/profiles/a.json';

test('B9 social发帖/回复/转帖投影到/public/social/posts，下一moment可grep；profile只含公开字段',async()=>{
 const run=await socialRun();let lease;
 try{
  await openMoment(run,'m1');
  const preferences=await run.ctx.actions.execute('social.preferences',{value:{secret:'私有偏好-不可公开',theme:'dark'}},context(run,{actorId:'a',momentId:'m1',operationId:'b9-pref'}));
  assert.equal(preferences.status,'completed');
  const published=await run.ctx.actions.execute('social.publish',{id:'p1',content:'B9公开帖子：价格观察',tags:['b9','price']},context(run,{actorId:'a',momentId:'m1',operationId:'b9-publish',logicalTime:day}));
  assert.equal(published.status,'completed');
  const reply=await run.ctx.actions.execute('social.reply',{id:'p1-reply',postId:'p1',content:'B9回复正文'},context(run,{actorId:'b',momentId:'m1',operationId:'b9-reply',logicalTime:day}));
  assert.equal(reply.status,'completed');
  const repost=await run.ctx.actions.execute('social.repost',{id:'p1-repost',postId:'p1'},context(run,{actorId:'c',momentId:'m1',operationId:'b9-repost',logicalTime:day}));
  assert.equal(repost.status,'completed');

  await assert.rejects(()=>readText(run,postFile,{momentId:'m1'}),error=>/EXPLORATION_(NOT_VISIBLE|NOT_FOUND)/.test(String(error))||/EXPLORATION_(NOT_VISIBLE|NOT_FOUND)/.test(error?.code??''),'同一moment的/public冻结视图不应看到刚提交的帖子');
  await assert.rejects(()=>readText(run,profileFile,{momentId:'m1'}),error=>/EXPLORATION_(NOT_VISIBLE|NOT_FOUND)/.test(String(error))||/EXPLORATION_(NOT_VISIBLE|NOT_FOUND)/.test(error?.code??''));

  await openMoment(run,'m2');
  const found=await grep(run,'B9公开帖子',{momentId:'m2'});
  assert.ok(found.items.some(item=>item.path===postFile),'下一moment应能在/public/social/posts/<day>.jsonl中grep到帖子');
  const lines=parseLines(await readText(run,postFile,{momentId:'m2'}));
  assert.deepEqual(lines.map(line=>line.id),['p1','p1-reply','p1-repost']);
  assert.deepEqual(lines.map(line=>line.kind),['post','reply','repost']);
  assert.equal(lines[0].authorId,'a');
  assert.deepEqual(lines[0].tags,['b9','price']);
  assert.equal(lines[1].parentId,'p1');

  const profile=JSON.parse(await readText(run,profileFile,{momentId:'m2'}));
  assert.equal(profile.id,'a');
  assert.equal(profile.attributes.name,'甲');
  assert.equal(profile.posts,'1');
  assert.equal(profile.preferences,undefined,'profile公开文档不应包含私有preferences');
  assert.equal(profile.unread,undefined,'profile公开文档不应包含未读数');
  assert.equal(profile.ordinal,undefined);
  assert.ok(!JSON.stringify(profile).includes('私有偏好-不可公开'));
 }finally{
  try{await lease?.release()}catch{}
  await run.reservation.release();await run.host.close();
 }
});

test('B9 推式写入随changeset fork恢复',async()=>{
 const table={path:'/public/social/posts',title:'帖子',description:'公开帖子',schema:{type:'object',properties:{id:{type:'string'},authorId:{type:'string'},content:{type:'string'},createdTime:{type:'string'}}},key:['id'],sort:[{field:'createdTime'},{field:'id'}],shard:{by:'createdTime',format:'day'}};
 const source=await setup({tables:[table]});let branch;let bytes;
 try{
  await source.tx('b9-fork-seed',tx=>source.ctx.public.writer(tx).upsert(table.path,[{id:'fork-1',authorId:'a',content:'B9 fork needle',createdTime:day}]));
  await source.openMoment('m','b9-fork-open');
  bytes=await readText({...source,runId:'b4'},postFile,{momentId:'m'});
  assert.match(bytes,/B9 fork needle/);
  await source.tx('b9-fork-point',tx=>source.ctx.database.metadata.complete(tx,frontier(),[]));
  const point=await source.seal();
  const target=await setup({tables:[table]});const directory=target.directory;await target.close();
  const names=(await readdir(join(source.directory,'segments'))).filter(name=>/^\d+\.changeset$/.test(name)).sort((a,b)=>Number(a.split('.')[0])-Number(b.split('.')[0]));
  const conn=openConnection({path:join(directory,'world.sqlite'),synchronous:'normal'});
  try{for(const name of names)conn.applySegment(join(source.directory,'segments',name))}finally{conn.close()}
  branch=await setup({directory,resume:point,tables:[table]});
  const after=await readText(branch,postFile,{momentId:'m'});
  assert.equal(after,bytes,'fork后推式写入逐字节一致');
 }finally{await branch?.close();await source.close()}
});
