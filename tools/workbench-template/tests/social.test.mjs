import { test } from 'node:test';
import assert from 'node:assert/strict';
import { socialFor, filterNetwork, filterPosts, actorActivity } from '../src/social.js';

const frame={nodes:[{id:'a',label:'甲',group:'学生'},{id:'b',label:'乙',group:'居民'},{id:'p',kind:'post'}],edges:[{id:'e',source:'a',target:'b',type:'follow'}],posts:[{id:'p',author:'b',content:'原文',tick:2,replies:[]}],feeds:{a:['p']}};
test('用户统计区分发帖、点赞给出和未记录',()=>{
 assert.deepEqual(actorActivity(frame,'b'),{posts:1,likesGiven:null});
 const recorded={...frame,posts:[{id:'p',author:'b',likes:['a']},{id:'q',author:'a',likes:[]}]};
 assert.deepEqual(actorActivity(recorded,'a'),{posts:1,likesGiven:1});
 assert.deepEqual(actorActivity(recorded,'b'),{posts:1,likesGiven:0});
 assert.equal(actorActivity({...frame,posts:[{id:'p',author:'b',likes:10}]},'b').likesGiven,null);
});
test('社交视图严格使用所选版本、运行与时点，不把未来快照带入过去',()=>{
 const version={social:{...frame,posts:[]}};
 const run={socialSnapshots:[{...frame,tickId:'2'}]};
 assert.equal(socialFor(version,run,'1'),null);
 assert.equal(socialFor(version,run,'2').posts.length,1);
 assert.equal(socialFor(version,null,'').posts.length,0);
});
test('网络图层和邻域使用同一组实际关系',()=>{
 assert.equal(filterNetwork(frame,{types:[],ego:'a'}).nodes.length,1);
 assert.equal(filterNetwork(frame,{types:['follow'],ego:'a'}).nodes.length,2);
});
test('发布与信息流分开查询，搜索保留完整帖子内容',()=>{
 assert.equal(filterPosts(frame,{actor:'a',mode:'authored'}).length,0);
 assert.equal(filterPosts(frame,{actor:'a',mode:'feed'})[0].content,'原文');
 assert.equal(filterPosts(frame,{query:'乙'}).length,1);
});
