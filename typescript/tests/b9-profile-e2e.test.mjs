import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {socialRun,context,openMoment,readText,parseLines} from './b9-support.mjs';
import {Prompts} from '../.build/core/dist/prompts/index.js';
import {ExplorationShell} from '../.build/core/dist/exploration-shell/index.js';
import ActionFiles from '../.build/core/dist/action-files/index.js';
import {ActorTools,actorToolsMigration} from '../.build/core/dist/actor-tools/index.js';

const day='1762387200';
const postFile='/public/social/posts/'+day+'.jsonl';
const notifications='/self/social/notifications.jsonl';
const flatten=entries=>entries.flatMap(entry=>entry.group?flatten(entry.config):[entry]);
const tool=(binding,name)=>{const found=binding.value.tools.find(item=>item.name===name);assert.ok(found,'missing tool '+name);return found};

async function configured(socialProfile='social-sqlite'){
 const profile=JSON.parse(await readFile(new URL('../profiles/'+socialProfile+'.json',import.meta.url),'utf8'));
 const entries=flatten(profile.patches[0].insert);
 const social=entries.find(entry=>entry.name==='@society0/social/social');
 const topology=entries.find(entry=>entry.name==='@society0/social/topology-edges');
 return {entries,social:social?.config??{},topology:topology?.config??{}};
}

test('B9 social-sqlite 端到端：act 发帖后下一 moment 可 grep，接收方 /self 通知可见',async t=>{
 const profile=await configured();
 assert.ok(profile.entries.some(entry=>entry.name==='@society0/core/action-files'),'social-sqlite ships action-files');
 assert.ok(profile.entries.some(entry=>entry.name==='@society0/core/public'),'social-sqlite ships public');
 const run=await socialRun({social:profile.social,actors:profile.topology.actors,edges:profile.topology.edges,migrations:[actorToolsMigration({consumer:'b9-profile-e2e'})]});
 t.after(async()=>{try{await run.reservation.release()}catch{}await run.host.close()});
 for(const plugin of [Prompts,ExplorationShell,ActionFiles,ActorTools])await run.ctx.plugin(plugin,{});

 await openMoment(run,'m1');
 const alice=await run.ctx.actorTools.bind(context(run,{actorId:'alice',momentId:'m1',operationId:'b9-e2e-alice',logicalTime:day}));
 const bob=await run.ctx.actorTools.bind(context(run,{actorId:'bob',momentId:'m1',operationId:'b9-e2e-bob',logicalTime:day}));
 try{
  const published=await tool(alice,'act').execute('e2e-publish',{action:'social.publish',input:{id:'e2e-p1',content:'B9 E2E 唯一帖子正文'}},run.reservation.signal);
  assert.equal(published.details.status,'completed',published.content[0].text);
  const replied=await tool(bob,'act').execute('e2e-reply',{action:'social.reply',input:{id:'e2e-r1',postId:'e2e-p1',content:'B9 E2E 回复'}},run.reservation.signal);
  assert.equal(replied.details.status,'completed',replied.content[0].text);
 }finally{await alice.release();await bob.release()}

 await openMoment(run,'m2');
 const reader=await run.ctx.actorTools.bind(context(run,{actorId:'alice',momentId:'m2',operationId:'b9-e2e-alice-m2',logicalTime:day}));
 try{
  const found=await tool(reader,'grep').execute('e2e-grep',{pattern:'B9 E2E 唯一帖子正文',root:'/public'},run.reservation.signal);
  const text=found.content[0].text;
  assert.match(text,new RegExp(postFile.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+':\\d+:'),'grep 输出指向 /public/social/posts/<day>.jsonl: '+text);
  assert.match(text,/B9 E2E 唯一帖子正文/);
 }finally{await reader.release()}

 const visible=parseLines(await readText(run,notifications,{actorId:'alice',momentId:'m2',operationId:'b9-e2e-notifications'}));
 assert.ok(visible.length>0,'接收方 alice 应看到通知');
 assert.ok(visible.every(row=>row.actorId==='alice'),'通知只属于接收方');
 assert.ok(visible.some(row=>row.kind==='reply'&&JSON.stringify(row).includes('e2e-p1')),'回复通知指向被回复的帖子');
});
