import {writeFileSync,mkdirSync,readFileSync} from 'node:fs';
import {nodes,edges} from '../network-prototypes/model.js';

// 演示资料单独生成；空白模板保持无预设学科、主体和实验记录。
const social=tick=>({
 tickId:String(tick),source:'合成演示资料，用于界面验收',
 nodes:nodes.filter(n=>n.kind!=='post'||n.tick<=tick).map(n=>({...n,entityId:n.kind==='agent'?n.id:undefined})),
 edges:edges.filter(e=>e.tick<=tick),
 posts:nodes.filter(n=>n.kind==='post'&&n.tick<=tick).map(n=>({id:n.id,title:n.label,author:n.author,content:n.body,tick:n.tick,
   replies:edges.filter(e=>e.type==='reply'&&e.target===n.id&&e.tick<=tick).map(e=>({id:e.id,author:e.source,content:e.body,tick:e.tick})),likes:[]})),
 feeds:Object.fromEntries(nodes.filter(n=>n.kind==='agent').map(n=>[n.id,nodes.filter(p=>p.kind==='post'&&p.tick<=tick).slice(0,3).map(p=>p.id)]))
});
const agents=nodes.filter(n=>n.kind==='agent');
const entities=[{id:'environment',name:'实验整体',kind:'overview',configPath:'/environment'},...agents.map((n,i)=>({id:n.id,name:n.label,kind:'llm-agent',group:n.group,configPath:`/agents/${i}`}))];
const config={environment:{type:'social-network',recommendations:3},agents:agents.map(n=>({id:n.id,name:n.label,persona:n.description,active:true}))};
const configFields={
 '/environment':{label:'环境设置',description:'设定社交环境与信息推荐。各主体的人设和参与状态可从左栏逐一检查。'},
 '/environment/type':{label:'环境类型',description:'本演示使用 social-network 表示社交网络环境，提供发帖、回复与关系记录。',effect:'更换环境类型需要同步检查实验代码与可用行为。'},
 '/environment/recommendations':{label:'推荐帖子数量',description:'每次信息流推荐的帖子数量，单位为篇。本演示以 3 篇为初始设定。',effect:'更多推荐会扩大信息接触范围，实际提示词长度取决于实验的信息组装方式。'},
 '/agents/*/id':{label:'主体标识',description:'在配置、社交关系和会话记录中引用该主体的唯一标识。',effect:'更名时需同步更新其他引用该主体的配置。'},
 '/agents/*/name':{label:'显示名称',description:'研究者在主体列表与帖子作者栏看到的名称。',effect:'名称在模型输入中的用途需按实验提示词核对。'},
 '/agents/*/persona':{label:'人设说明',description:'描述该主体的背景、关注点与行为倾向，供实验构建模型输入时使用。',effect:'会影响主体的角色设定；建议以新配置版本比较调整前后的表现。'},
 '/agents/*/active':{label:'参与实验',description:'本演示用此开关记录主体是否纳入实验参与者。',effect:'停用后的调度、历史关系与记录保留方式需由实验代码落实。'}
};
const demoViews=[
 {id:'text',title:'说明',type:'text',text:'合成资料：检查多种呈现方式与完整原文。'},
 {id:'metric',title:'指标',type:'metric',items:[{label:'主体数',value:agents.length}]},
 {id:'series',title:'时间序列',type:'timeseries',rows:[{x:'1',y:2},{x:'2',y:5},{x:'3',y:8}]},
 {id:'ohlc',title:'K 线',type:'candlestick',rows:[{x:'1',open:2,high:4,low:1,close:3},{x:'2',open:3,high:5,low:2,close:4}]},
 {id:'table',title:'表格',type:'table',columns:[{key:'record',label:'记录'}],rows:Array.from({length:1000},(_,i)=>({id:i,record:`记录 ${i}`}))},
 {id:'network',title:'网络模块',type:'network',nodes:[{id:'a',label:'甲'},{id:'b',label:'乙'}],edges:[{from:'a',to:'b'}]},
];
const makeRun=id=>({id,name:id==='pilot-001'?'试运行 1':'试运行 2',ticks:[0,1,2,3,4].map(t=>({id:String(t),label:t?`Tick ${t}`:'初始配置'})),socialSnapshots:[0,1,2,3,4].map(social),snapshots:[0,1,2,3,4].flatMap(t=>entities.map(e=>({tickId:String(t),entityId:e.id,
 mechanisms:e.kind==='llm-agent'?[{id:'feed',name:'信息流',access:t?'available':'unavailable',description:'按配置提供的信息流入口。',reason:t?'已保存该时点的信息流资料':'初始配置尚无帖子',source:'合成机制说明',views:[{id:'info',type:'text',text:'可接触范围与会话输入分别记录。'}]}]:[],
 sessions:e.kind==='llm-agent'&&t?[1,2].map(i=>({id:`${e.id}-${t}-${i}`,label:`第 ${i} 次交互`,events:[{kind:'input',text:'合成输入记录',source:'演示数据'},{kind:'output',text:`${e.name} 的合成回应 ${i}`,source:'演示数据'}]})):[],
 tabs:[{id:'results',title:'运行产物',modules:[{id:'all',title:'组件检查',views:demoViews,source:'合成资料'}]}],
 sideTabs:[{id:'analysis',title:'分析',modules:[{id:'summary',title:'来源说明',views:[{id:'text',type:'text',text:'这里可放置 agent 根据已完成实验撰写的分析。'}]}]}]
})))});
const data={study:{title:'夜间公共交通讨论 · 合成演示',question:'不同群体如何交换公共交通信息？',defaultView:'network'},versions:[
 {id:'v001',name:'v001',config,configFields,entities,runs:[makeRun('pilot-001'),makeRun('pilot-002')],social:social(0),configSource:'versions/v001/config.json'},
 {id:'v002',name:'v002 · 待试运行',configFields,config:{...config,environment:{type:'social-network',recommendations:5}},entities,runs:[],social:social(0),configSource:'versions/v002/config.json'}]};
for(const frame of data.versions[0].runs[1].socialSnapshots){
 frame.source='试运行 2 · 独立合成资料';
 frame.posts=frame.posts.map(p=>({...p,content:`【试运行 2 的合成记录】${p.content}`}));
}
mkdirSync('dist/demo',{recursive:true});writeFileSync('dist/demo/data.json',JSON.stringify(data,null,2));
const template=readFileSync('dist/standalone/index.html','utf8');
writeFileSync('dist/demo/workbench.html',template.replace(/(<script id="society0-workbench-data" type="application\/json">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+JSON.stringify(data).replaceAll('<','\\u003c')+b));
