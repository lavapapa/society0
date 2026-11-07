import {test} from 'node:test';
import assert from 'node:assert/strict';
import {socialRun,context,openMoment,readText,parseLines} from './b9-support.mjs';

const notifications='/self/social/notifications.jsonl';
const following='/self/social/following.jsonl';
const followers='/self/social/followers.jsonl';
const profile='/self/profile.json';

test('B9 /self/social/*按主体实时反映通知与关注，B看不到A的内容',async()=>{
 const run=await socialRun();
 try{
  await openMoment(run,'m1');
  await run.ctx.actions.execute('social.preferences',{value:{secret:'A的私有偏好',theme:'light'}},context(run,{actorId:'a',momentId:'m1',operationId:'b9-self-pref'}));
  await run.ctx.actions.execute('social.publish',{id:'self-p1',content:'A的公开帖子'},context(run,{actorId:'a',momentId:'m1',operationId:'b9-self-p1',logicalTime:'1'}));
  await run.ctx.actions.execute('social.reply',{id:'self-p1-reply',postId:'self-p1',content:'B的回复'},context(run,{actorId:'b',momentId:'m1',operationId:'b9-self-reply',logicalTime:'2'}));
  await run.ctx.actions.execute('social.follow',{actorId:'b'},context(run,{actorId:'a',momentId:'m1',operationId:'b9-self-follow',logicalTime:'3'}));

  const aNotifications=parseLines(await readText(run,notifications,{actorId:'a',momentId:'m1',operationId:'b9-read-a-notes'}));
  assert.ok(aNotifications.some(row=>row.kind==='reply'&&row.actorId==='a'),'A应看到B回复产生的通知');
  assert.ok(aNotifications.every(row=>row.actorId==='a'),'A的/self通知只能属于A');

  const aFollowing=parseLines(await readText(run,following,{actorId:'a',momentId:'m1',operationId:'b9-read-a-following'}));
  assert.deepEqual(aFollowing.map(row=>[row.from,row.to]),[['a','b']]);
  const aFollowers=parseLines(await readText(run,followers,{actorId:'a',momentId:'m1',operationId:'b9-read-a-followers'}));
  assert.deepEqual(aFollowers.map(row=>row.from).sort(),['b','c']);

  const aProfile=JSON.parse(await readText(run,profile,{actorId:'a',momentId:'m1',operationId:'b9-read-a-profile'}));
  assert.equal(aProfile.id,'a');
  assert.deepEqual(aProfile.preferences,{secret:'A的私有偏好',theme:'light'});

  const bNotificationsText=await readText(run,notifications,{actorId:'b',momentId:'m1',operationId:'b9-read-b-notes'});
  const bNotifications=parseLines(bNotificationsText);
  assert.ok(bNotifications.every(row=>row.actorId==='b'));
  assert.ok(!bNotificationsText.includes('self-p1-reply'),'B的/self通知不应包含A的通知');
  const bFollowing=parseLines(await readText(run,following,{actorId:'b',momentId:'m1',operationId:'b9-read-b-following'}));
  assert.ok(bFollowing.every(row=>row.from==='b'));
  assert.ok(!bFollowing.some(row=>row.from==='a'),'B不应看到A的关注边');
  const bProfile=JSON.parse(await readText(run,profile,{actorId:'b',momentId:'m1',operationId:'b9-read-b-profile'}));
  assert.equal(bProfile.id,'b');
  assert.ok(!JSON.stringify(bProfile).includes('A的私有偏好'));
 }finally{await run.reservation.release();await run.host.close()}
});
