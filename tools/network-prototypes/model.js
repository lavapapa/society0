export const layers = {
  follow: {label:'关注',color:'#8b99aa',meaning:'智能体 → 被关注的智能体；持续关系'},
  publish: {label:'发布',color:'#446bd4',meaning:'作者 → 帖子；发布事件'},
  reply: {label:'评论',color:'#b66c35',meaning:'评论者 → 帖子；评论事件'},
  share: {label:'转发',color:'#28826d',meaning:'转发者 → 帖子；转发事件'},
};
const names = ['林澈','周宁','陈亦','许禾','沈言','陆遥','苏晴','方青','叶知','宋川','顾安','唐悦','吴越','程溪','梁夏','何沐','孟舟','江岚'];
const groups = ['学生','社区居民','公共机构'];
export const nodes = names.map((label,i)=>({
  id:`a${i+1}`,label,kind:'agent',group:groups[Math.floor(i/6)],tick:0,
  description:['关心公共议题中的信息来源与可信度。','倾向于先阅读讨论，再询问证据。','关注公共服务如何影响日常生活。'][Math.floor(i/6)],
  x:[150,490,830][Math.floor(i/6)]+Math.cos((i%6)*Math.PI/3)*95,
  y:175+Math.sin((i%6)*Math.PI/3)*100,
}));
const posts = [
  ['夜间公交是否需要延长？','末班车之后，校园到社区的接驳仍有缺口。大家通常怎样回家？','a1',1,320,60],
  ['先看看实际出行需求','我记录了几次末班车的乘坐情况。想了解周末与工作日是否不同。','a7',1,320,280],
  ['新增站点的讨论','如果增设社区入口站点，步行距离可能缩短。也想听听周边居民的意见。','a13',2,660,60],
  ['夜间照明的另一种顾虑','有居民提到站点附近的照明问题。线路与站点的讨论能否一起考虑？','a9',2,660,280],
  ['关于试行时段的建议','可以先讨论周五与周六的试行时段，再记录实际需求。','a4',3,150,350],
  ['征集线路反馈','欢迎补充具体的起终点、时间和候车经历，方便整理讨论。','a16',4,830,350],
];
posts.forEach(([label,body,author,tick,x,y],i)=>nodes.push({id:`p${i+1}`,label,body,author,tick,x,y,kind:'post',group:'帖子'}));
export const edges=[];
function add(source,target,type,tick,body){edges.push({id:`e${edges.length+1}`,source,target,type,tick,body:body||layers[type].meaning});}
for(let i=0;i<18;i++) add(`a${i+1}`,`a${Math.floor(i/6)*6+(i+1)%6+1}`,'follow',0);
[['a1','a7'],['a7','a13'],['a13','a1'],['a4','a10'],['a10','a16'],['a16','a4'],['a2','a9'],['a8','a14']].forEach(([s,t])=>add(s,t,'follow',0));
nodes.filter(n=>n.kind==='post').forEach(p=>add(p.author,p.id,'publish',p.tick,p.body));
[
 ['a7','p1','reply',1,'我也遇到过相似情况，周末尤其明显。'],['a2','p1','share',1,'转给同学一起补充出行经历。'],
 ['a13','p1','reply',2,'能否补充通常的候车时间？'],['a3','p2','reply',2,'周五可能与其他工作日差别很大。'],['a10','p1','share',2,'社区里也有人在讨论这个问题。'],
 ['a1','p3','reply',3,'社区入口的站点对晚归学生也有帮助。'],['a8','p3','share',3,'转发给附近居民。'],['a14','p4','reply',3,'照明可以单独收集具体位置。'],['a5','p2','reply',3,'希望看到按时段整理的记录。'],
 ['a16','p5','reply',4,'试行时段可以作为下一轮讨论的选项。'],['a11','p4','share',4,'补充一个夜间照明的视角。'],['a6','p6','reply',4,'我通常在晚间十点以后从校园返回。'],['a12','p6','reply',4,'社区入口到现有站点步行大约十分钟。'],['a1','p6','share',4,'转给此前参与讨论的同学。'],
].forEach(args=>add(...args));
export const byId=Object.fromEntries([...nodes,...edges].map(x=>[x.id,x]));
export function visibleGraph({tick=4,layers:enabled=Object.keys(layers),scope='cumulative',group='全部',ego=null}={}) {
  let ns=nodes.filter(n=>n.tick<=tick&&(group==='全部'||n.group===group||n.kind==='post'));
  const ids=new Set(ns.map(n=>n.id));
  let es=edges.filter(e=>ids.has(e.source)&&ids.has(e.target)&&enabled.includes(e.type)&&e.tick<=tick&&(scope!=='moment'||e.type==='follow'||e.tick===tick));
  const postIds=new Set(es.flatMap(e=>[e.source,e.target]));
  ns=ns.filter(n=>n.kind==='agent'||postIds.has(n.id));
  if(ego){es=es.filter(e=>e.source===ego||e.target===ego);const nearby=new Set([ego,...es.flatMap(e=>[e.source,e.target])]);ns=ns.filter(n=>nearby.has(n.id));}
  return {nodes:ns,edges:es};
}
