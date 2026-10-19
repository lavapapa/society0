import React,{useState,useEffect,useLayoutEffect,useRef,useMemo} from 'react';
import {createRoot} from 'react-dom/client';
import cytoscape from 'cytoscape';
import {IconSearch,IconPlus,IconMinus,IconFocus2,IconTarget,IconLayoutGrid,IconX,IconAdjustmentsHorizontal,IconArrowRight,IconDownload,IconEye,IconChevronLeft,IconChevronRight,IconUser,IconFileText,IconNetwork,IconList,IconRotateClockwise,IconLayoutSidebarRight,IconExternalLink} from '@tabler/icons-react';
import {nodes,edges,layers,byId,visibleGraph} from './model.js';
import './style.css';
import './picker.css';

const variants=['研究台','探索画布','事件追踪'];
const colors={'学生':'#496eae','社区居民':'#388879','公共机构':'#9b784b','帖子':'#ffffff'};
const label=id=>byId[id]?.label||id;
function visibleNeighborhood(cy,el,graph){if(el.isEdge())return el.connectedNodes().union(el);const ids=new Set(graph.edges.map(e=>e.id));const es=el.connectedEdges().filter(e=>ids.has(e.id()));return es.connectedNodes().union(es).union(el);}
const glyph=(Icon)=><Icon size={17} stroke={1.7}/>;
function Button({icon:Icon,children,...props}){return <button {...props}>{Icon&&glyph(Icon)}{children}</button>;}

function Picker({current,onChange,onReplay}){
 const ref=useRef(null);
 useLayoutEffect(()=>{const p=ref.current;const move=()=>{const b=p.querySelectorAll('.proto-picker-item')[current],h=p.querySelector('.proto-picker-highlight');h.style.width=b.offsetWidth+'px';h.style.transform=`translateX(${b.offsetLeft}px)`;};move();window.addEventListener('resize',move);requestAnimationFrame(()=>requestAnimationFrame(()=>p.setAttribute('data-ready','')));return()=>window.removeEventListener('resize',move);},[current]);
 useEffect(()=>{const key=e=>{if(e.defaultPrevented||/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)||e.target.isContentEditable||e.metaKey||e.ctrlKey||e.altKey)return;const n=parseInt(e.key,10);if(n>=1&&n<=3)onChange(n-1);else if(e.key==='ArrowRight')onChange((current+1)%3);else if(e.key==='ArrowLeft')onChange((current+2)%3);else if(e.key.toLowerCase()==='r')onReplay();};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[current,onChange,onReplay]);
 return <nav ref={ref} className="proto-picker" aria-label="Prototype variants"><span className="proto-picker-highlight" aria-hidden="true"/>{variants.map((name,i)=><button key={name} className="proto-picker-item" data-active={i===current?'':undefined} aria-current={i===current?'true':undefined} onClick={()=>onChange(i)}>{name}</button>)}</nav>;
}

function Network({graph,selected,onSelect,cyRef,layout,labels,variant}){
 const host=useRef(null),selectRef=useRef(onSelect);selectRef.current=onSelect;
 useEffect(()=>{
  const cy=cytoscape({container:host.current,elements:[...nodes.map(n=>({data:{...n,color:colors[n.group],short:n.kind==='post'?n.id.toUpperCase():n.label},position:{x:n.x,y:n.y}})),...edges.map(e=>({data:{...e,color:layers[e.type].color}}))],layout:{name:'preset'},minZoom:.25,maxZoom:3,wheelSensitivity:.2,selectionType:'single',style:[
   {selector:'node',style:{'background-color':'data(color)','width':30,'height':30,'label':'data(short)','font-family':'-apple-system, BlinkMacSystemFont, PingFang SC, sans-serif','font-size':12,'color':'#344251','text-valign':'bottom','text-margin-y':8,'border-width':2,'border-color':'#ffffff','text-background-color':'#fafbfd','text-background-opacity':.85,'text-background-padding':2}},
   {selector:'node[kind="post"]',style:{shape:'round-rectangle',width:32,height:26,'border-color':'#92a3ba','border-width':1.5,'text-valign':'center','text-margin-y':0,'font-size':10,'text-background-opacity':0}},
   {selector:'edge',style:{width:1.4,'line-color':'data(color)','target-arrow-color':'data(color)','target-arrow-shape':'triangle','arrow-scale':.7,'curve-style':'bezier',opacity:.68}},
   {selector:'edge[type="follow"]',style:{'line-style':'dashed',width:1,opacity:.43}},
   {selector:'.muted',style:{opacity:.13,'text-opacity':.25}},
   {selector:'.linked',style:{opacity:1,width:2.4}},
   {selector:'.chosen',style:{'border-color':'#244dcc','border-width':4,'line-color':'#244dcc','target-arrow-color':'#244dcc',opacity:1}},
   {selector:'.hidden',style:{display:'none'}},
   {selector:'node.no-label',style:{label:''}},
  ]});
  cyRef.current=cy;
  const readable=()=>{const z=cy.zoom();cy.batch(()=>{cy.nodes().style({'font-size':11/z,width:22/z,height:22/z,'text-margin-y':6/z});cy.nodes('[kind="post"]').style({'font-size':10/z,width:25/z,height:20/z,'text-margin-y':0});});};
  cy.on('zoom',readable);readable();
  cy.on('tap','node,edge',e=>selectRef.current(e.target.id()));
  cy.on('tap',e=>{if(e.target===cy)selectRef.current(null);});
  const ro=new ResizeObserver(()=>{cy.resize();});ro.observe(host.current);
  return()=>{ro.disconnect();cy.destroy();cyRef.current=null;};
 },[]);
 useEffect(()=>{
  const cy=cyRef.current,ids=new Set([...graph.nodes,...graph.edges].map(n=>n.id));
  cy.batch(()=>{cy.elements().forEach(e=>e.toggleClass('hidden',!ids.has(e.id())));});
 },[graph]);
 useEffect(()=>{
  const cy=cyRef.current;cy.elements().removeClass('chosen muted linked');
  const el=selected&&cy.getElementById(selected);if(el?.length){const near=visibleNeighborhood(cy,el,graph);cy.elements().difference(near).addClass('muted');near.edges().addClass('linked');el.addClass('chosen');}
 },[selected,graph]);
 useEffect(()=>{cyRef.current.nodes().toggleClass('no-label',!labels);},[labels]);
 useEffect(()=>{
  const cy=cyRef.current;
  if(layout==='preset')cy.nodes().positions(n=>({x:byId[n.id()].x,y:byId[n.id()].y}));
  else cy.elements(':visible').layout({name:layout,animate:false,randomize:false,padding:55,nodeRepulsion:10000,idealEdgeLength:110,componentSpacing:100}).run();
  cy.fit(cy.elements(':visible'),20);
 },[layout,variant]);
 return <div ref={host} className="network" role="img" aria-label="社交关系网络；可拖动节点，滚轮缩放。键盘用户可从对象列表选择。"/>;
}

function Explorer({variant,observation,setObservation}){
 const shared=key=>[observation[key],value=>setObservation(old=>({...old,[key]:typeof value==='function'?value(old[key]):value}))];
 const [tick,setTick]=shared('tick'),[scope,setScope]=shared('scope'),[enabled,setEnabled]=shared('enabled'),[group,setGroup]=shared('group');
 const [selected,setSelected]=shared('selected'),[query,setQuery]=shared('query'),[ego,setEgo]=shared('ego'),[layout,setLayout]=shared('layout'),[labels,setLabels]=shared('labels'),[filterOpen,setFilterOpen]=useState(false),[detailOpen,setDetailOpen]=useState(variant!==1||!!selected),[detailTab,setDetailTab]=useState('overview'),[zoom,setZoom]=useState(100),[notice,setNotice]=useState('');
 const cyRef=useRef(null);
 const graph=useMemo(()=>visibleGraph({tick,layers:enabled,scope,group,ego}),[tick,enabled,scope,group,ego]);
 const item=byId[selected],visibleIds=new Set([...graph.nodes,...graph.edges].map(n=>n.id));
 const available=graph.nodes.filter(n=>(n.label+n.id).toLowerCase().includes(query.toLowerCase()));
 const activity=graph.edges.filter(e=>e.type!=='follow').sort((a,b)=>b.tick-a.tick||b.id.localeCompare(a.id));
 const records=item?(item.source? [item] : graph.edges.filter(e=>e.source===selected||e.target===selected)):[];
 function choose(id){setSelected(id);setDetailOpen(!!id);setDetailTab('overview');}
 function fit(){const cy=cyRef.current;if(graph.nodes.length)cy.fit(cy.elements(':visible'),20);}
 function focus(id=selected){const cy=cyRef.current;if(id&&visibleIds.has(id)){const near=visibleNeighborhood(cy,cy.getElementById(id),graph);cy.fit(near,24);cy.zoom(Math.min(cy.zoom(),1.7));cy.center(near);}}
 useEffect(()=>{const cy=cyRef.current;const update=()=>setZoom(Math.round(cy.zoom()*100));cy.on('zoom',update);update();return()=>cy.off('zoom',update);},[]);
 useEffect(()=>{if(selected&&!visibleIds.has(selected)){setSelected(null);if(variant===1)setDetailOpen(false);}},[graph]);
 useEffect(()=>{if(!notice)return;const id=setTimeout(()=>setNotice(''),2500);return()=>clearTimeout(id);},[notice]);
 function exportView(){const a=document.createElement('a');a.href=cyRef.current.png({bg:'#fafbfd',full:false,scale:2});a.download=`society0-network-tick-${tick}.png`;a.click();setNotice('网络图片已导出');}
 const layerControls=<div className="layer-list">{Object.entries(layers).map(([key,l])=><label key={key} title={l.meaning}><input type="checkbox" checked={enabled.includes(key)} onChange={()=>setEnabled(v=>v.includes(key)?v.filter(k=>k!==key):[...v,key])}/><span className={'line-key '+key} style={{background:l.color}}/><span>{l.label}</span><small>{graph.edges.filter(e=>e.type===key).length}</small></label>)}</div>;
 const search=<label className="search">{glyph(IconSearch)}<input aria-label="查找对象" placeholder="查找智能体或帖子" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="清除搜索" onClick={()=>setQuery('')}>{glyph(IconX)}</button>}</label>;
 const objects=<div className="object-list">{available.map(n=><button key={n.id} className={selected===n.id?'active':''} onClick={()=>{choose(n.id);focus(n.id);}}><span className={'avatar '+n.kind} style={{'--node-color':colors[n.group]}}>{n.kind==='agent'?n.label.slice(0,1):glyph(IconFileText)}</span><span>{n.label}<small>{n.kind==='agent'?n.group:n.id.toUpperCase()}</small></span></button>)}{!available.length&&<p className="empty-inline">没有匹配的对象。试试其他名称或放宽筛选。</p>}</div>;
 const filterPanel=<><div className="section-head"><h3>关系图层</h3><button className="text-button" onClick={()=>setEnabled(Object.keys(layers))}>全选</button></div>{layerControls}<label className="field">主体分组<select aria-label="主体分组" value={group} onChange={e=>setGroup(e.target.value)}>{['全部','学生','社区居民','公共机构'].map(x=><option key={x}>{x}</option>)}</select></label><p className="small-note">关注保留当前状态；发布、评论与转发按时间范围显示。</p></>;
 const recordButton=e=><button key={e.id} className={'record '+(selected===e.id?'active':'')} onClick={()=>{choose(e.id);focus(e.id);}}><span className="record-top"><span style={{color:layers[e.type].color}}>{layers[e.type].label}</span><small>Tick {e.tick}</small></span><strong>{label(e.source)} <IconArrowRight size={12}/> {label(e.target)}</strong><p>{e.body}</p></button>;
 return <div className={`app variant-${variant+1}`} onKeyDown={e=>{if(e.target.getAttribute('role')==='tab'&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();e.stopPropagation();const next=e.key==='Home'?'overview':e.key==='End'?'records':detailTab==='overview'?'records':'overview';setDetailTab(next);e.target.parentElement.querySelectorAll('button')[next==='overview'?0:1].focus();}}}>
  <header className="topbar"><div className="brand">{glyph(IconNetwork)}<strong>Society0</strong><span className="divider"/><span>网络工作台</span></div><div className="project-name">夜间公共交通讨论 <span className="sample">合成示例</span></div><div className="context">v001 <span>/</span> pilot-001 <span>/</span> 保存的快照</div></header>
  <div className="workspace">
   {variant===0&&<aside className="left-panel"><div className="panel-pad">{filterPanel}<div className="section-head"><h3>对象</h3><small>{available.length}</small></div>{search}</div>{objects}</aside>}
   {variant===2&&<aside className="event-panel"><div className="panel-pad"><h2>交互记录</h2><p className="small-note">点击记录，在网络中查看它的两端。</p><label className="field">记录范围<select aria-label="记录范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="cumulative">截至当前 tick</option><option value="moment">当前 tick</option></select></label></div><div className="event-list">{activity.map(recordButton)}{!activity.length&&<p className="empty-inline">当前范围没有交互记录。可以切换到后续 tick 或开启关系图层。</p>}</div></aside>}
   <main className="canvas-area">
    <div className="canvas-heading"><div><strong>{ego?`${label(ego)}的一阶邻域`:'社交网络'}</strong><small>{graph.nodes.length} 对象 · {graph.edges.length} 关系</small></div><Button icon={IconDownload} aria-label="导出图片" onClick={exportView}/>{ego&&<button onClick={()=>setEgo(null)}>返回全网</button>}{variant!==0&&<Button icon={IconAdjustmentsHorizontal} aria-expanded={filterOpen} onClick={()=>setFilterOpen(!filterOpen)}>图层与对象</Button>}</div>
    <div className="graph-frame">
     <Network graph={graph} selected={selected} onSelect={choose} cyRef={cyRef} layout={layout} labels={labels} variant={variant}/>
     {variant!==0&&filterOpen&&<aside className="filter-popover"><div className="section-head"><h3>筛选网络</h3><Button icon={IconX} aria-label="关闭筛选" onClick={()=>setFilterOpen(false)}/></div>{filterPanel}{search}{objects}</aside>}
     {variant===1&&!filterOpen&&<div className="canvas-search">{search}{query&&<div className="search-results">{objects}</div>}</div>}
     {!graph.nodes.length&&<div className="graph-empty"><h3>当前筛选没有对象</h3><button onClick={()=>{setGroup('全部');setEgo(null);setEnabled(Object.keys(layers));}}>恢复全部图层</button></div>}
     <div className="float-toolbar" aria-label="网络工具条"><Button icon={IconPlus} aria-label="放大" title="放大" onClick={()=>{const c=cyRef.current;c.zoom({level:Math.min(3,c.zoom()*1.25),renderedPosition:{x:c.width()/2,y:c.height()/2}});}}/><span className="zoom-level">{zoom}%</span><Button icon={IconMinus} aria-label="缩小" title="缩小" onClick={()=>{const c=cyRef.current;c.zoom({level:Math.max(.25,c.zoom()/1.25),renderedPosition:{x:c.width()/2,y:c.height()/2}});}}/><span className="tool-divider"/><Button icon={IconFocus2} title="适应全部内容" onClick={fit}>全图</Button><Button icon={IconTarget} disabled={!selected} title="定位选中对象及其关联" onClick={()=>focus()}>定位</Button><span className="tool-divider"/><label title="布局方式" className="layout-control">{glyph(IconLayoutGrid)}<select aria-label="布局方式" value={layout} onChange={e=>setLayout(e.target.value)}><option value="preset">分组布局</option><option value="cose">力导向</option><option value="circle">环形布局</option></select></label><Button icon={IconRotateClockwise} aria-label="重新排列" title="重新排列当前布局" onClick={()=>{const c=cyRef.current;if(layout==='preset'){c.nodes().positions(n=>({x:byId[n.id()].x,y:byId[n.id()].y}));fit();}else c.elements(':visible').layout({name:layout,animate:false,randomize:false,padding:55,nodeRepulsion:10000,idealEdgeLength:110}).run();}}/><Button icon={IconEye} aria-label="显示标签" aria-pressed={labels} className={labels?'active':''} title="显示或隐藏标签" onClick={()=>setLabels(!labels)}/></div>
     <div className="canvas-legend"><span><i style={{background:colors['学生']}}/>学生</span><span><i style={{background:colors['社区居民']}}/>居民</span><span><i style={{background:colors['公共机构']}}/>机构</span><span><i className="post-key"/>帖子</span></div>
     <div className="canvas-help">拖动画布平移 · 滚轮缩放 · 点击节点或连线查看详情</div>
     {variant===1&&!detailOpen&&<Button className="show-detail" icon={IconLayoutSidebarRight} onClick={()=>setDetailOpen(true)}>详情</Button>}
    </div>
    {variant===2&&<div className="relation-strip">{Object.entries(layers).map(([key,l])=><button key={key} aria-pressed={enabled.includes(key)} className={enabled.includes(key)?'active':''} onClick={()=>setEnabled(v=>v.includes(key)?v.filter(k=>k!==key):[...v,key])}><span className={'line-key '+key} style={{background:l.color}}/>{l.label}<small>{graph.edges.filter(e=>e.type===key).length}</small></button>)}</div>}
   </main>
   {detailOpen&&<aside className="detail-panel"><div className="detail-heading"><h2>对象详情</h2>{variant===1&&<Button icon={IconX} aria-label="关闭详情" onClick={()=>setDetailOpen(false)}/>}</div>{item?<><div className="identity"><span className={'identity-icon '+(item.kind||'edge')}>{glyph(item.kind==='agent'?IconUser:item.kind==='post'?IconFileText:IconNetwork)}</span><div><small>{item.kind==='agent'?'LLM 智能体':item.kind==='post'?'帖子':`${layers[item.type].label}关系`} · {item.id}</small><h2>{item.source?`${label(item.source)} → ${label(item.target)}`:item.label}</h2></div></div><div className="detail-tabs" role="tablist" aria-label="详情内容">{[['overview','概览'],['records',`关联记录 ${records.length}`]].map(([id,name])=><button key={id} role="tab" aria-selected={detailTab===id} onClick={()=>setDetailTab(id)}>{name}</button>)}</div><div className="detail-body">{detailTab==='overview'?<>
    <p className="description">{item.body||item.description}</p>
    <dl><dt>观察时点</dt><dd>{tick===0?'初始配置':`Tick ${tick}`}</dd><dt>数据来源</dt><dd>合成交互样例</dd>{item.source?<><dt>关系方向</dt><dd>{label(item.source)} → {label(item.target)}</dd><dt>发生时点</dt><dd>Tick {item.tick}</dd></>:<><dt>{item.kind==='agent'?'主体分组':'发布者'}</dt><dd>{item.group==='帖子'?label(item.author):item.group}</dd><dt>{item.kind==='agent'?'当前可见关系':'发布时点'}</dt><dd>{item.kind==='agent'?`${records.length} 条`:`Tick ${item.tick}`}</dd></>}</dl>
    {item.source?<div className="endpoint-buttons"><Button icon={IconUser} onClick={()=>{choose(item.source);focus(item.source);}}>查看来源</Button><Button icon={IconExternalLink} onClick={()=>{choose(item.target);focus(item.target);}}>查看目标</Button></div>:<Button className="primary" icon={IconTarget} onClick={()=>{setEgo(ego===item.id?null:item.id);}}>{ego===item.id?'返回完整网络':'仅看一阶邻域'}</Button>}
    <div className="detail-section"><h3>{item.kind==='agent'?'阅读线索':'记录说明'}</h3><p>{item.kind==='agent'?'选中对象的直接关系已加深显示。可切换到关联记录，逐条检查关系方向与交互内容。':item.kind==='post'?'发布、评论和转发分别保留独立连线。点击关联记录可查看具体参与者。':'此处保留该关系的具体端点和内容，可继续进入任一端点查看。'}</p></div>
    <p className="small-note">页面展示保存的网络资料。合成样例用于比较交互方案。</p>
   </>:<div className="related-records">{records.map(recordButton)}{!records.length&&<p>当前筛选范围内没有关联记录。</p>}</div>}</div></>:<div className="detail-empty">{glyph(IconTarget)}<h3>选择一个对象或关系</h3><p>点击画布中的节点、连线，或从对象列表与交互记录进入。</p>{variant!==0&&<button onClick={()=>setFilterOpen(true)}>打开对象列表</button>}</div>}</aside>}
  </div>
  <footer className="timeline"><div className="timeline-label"><strong>{tick===0?'初始配置':`Tick ${tick}`}</strong><small>保存的时点</small></div><Button icon={IconChevronLeft} aria-label="上一个时点" disabled={tick===0} onClick={()=>setTick(tick-1)}/><div className="tick-slider"><input aria-label="实验时点" type="range" min="0" max="4" step="1" value={tick} onChange={e=>setTick(Number(e.target.value))}/><div><span>配置</span><span>1</span><span>2</span><span>3</span><span>4</span></div></div><Button icon={IconChevronRight} aria-label="下一个时点" disabled={tick===4} onClick={()=>setTick(tick+1)}/><label className="scope-control">事件范围<select aria-label="事件范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="cumulative">累计至此时点</option><option value="moment">仅当前时点</option></select></label><span className="offline">静态快照 · 离线浏览</span></footer>
  <div className="prototype-caption">交互原型 / {variants[variant]} <span>按 1、2、3 比较方案</span></div>{notice&&<div role="status" className="notice">{notice}</div>}
 </div>;
}
function App(){const [variant,setVariant]=useState(()=>Math.max(0,Math.min(2,(parseInt(new URLSearchParams(location.search).get('v'),10)||1)-1))),[replay,setReplay]=useState(0);const [observation,setObservation]=useState({tick:4,scope:'cumulative',enabled:Object.keys(layers),group:'全部',selected:null,query:'',ego:null,layout:'preset',labels:true});function change(i){if(i===variant)return;setVariant(i);const u=new URL(location);u.searchParams.set('v',i+1);history.replaceState(null,'',u);}return <><Explorer key={`${variant}-${replay}`} variant={variant} observation={observation} setObservation={setObservation}/><Picker current={variant} onChange={change} onReplay={()=>setReplay(x=>x+1)}/></>;}
createRoot(document.getElementById('root')).render(<App/>);
