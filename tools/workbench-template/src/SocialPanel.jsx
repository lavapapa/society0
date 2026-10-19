import React, {useEffect,useMemo,useRef,useState} from 'react';
import {IconLayoutSidebarLeftExpand,IconLayoutSidebarRightExpand} from '@tabler/icons-react';
import cytoscape from 'cytoscape';
import {IconPlus,IconMinus,IconFocus2,IconTarget,IconRotateClockwise,IconDownload,IconX,IconLayoutSidebarLeftCollapse,IconLayoutSidebarRightCollapse,IconArrowsMaximize,IconUsers,IconStack2,IconSearch,IconMessageCircle,IconHeart,IconFileText,IconNetwork,IconUserSearch} from '@tabler/icons-react';
import {filterNetwork,filterPosts,actorActivity} from './social';
import {visibleRows} from './model';

const palette=['#4971b3','#418775','#a17d48','#936c9f','#698593'];
const typeNames={follow:'关注',publish:'发布',reply:'评论',share:'转发',like:'点赞'};
function Action({label,Icon,onClick,disabled}){return <button title={label} aria-label={label} onClick={onClick} disabled={disabled}><Icon size={17}/></button>;}

function VirtualList({items,height=40,render,label}) {
 const ref=useRef(null),[top,setTop]=useState(0),[viewport,setViewport]=useState(500);
 useEffect(()=>{const r=new ResizeObserver(()=>setViewport(ref.current?.clientHeight || 500));r.observe(ref.current);return()=>r.disconnect();},[]);
 useEffect(()=>{ref.current.scrollTop=0;setTop(0);},[items]);
 const {start,end}=visibleRows(items.length,top,viewport,height,4);
 return <div className="social-list" ref={ref} aria-label={label} onScroll={e=>setTop(e.currentTarget.scrollTop)}><div style={{height:items.length*height,position:'relative'}}>{items.slice(start,end).map((item,i)=><div key={item.id} style={{position:'absolute',top:(start+i)*height,height,left:0,right:0}}>{render(item)}</div>)}</div></div>;
}

export function SocialPanel({frame,mode,onMode,onInspect,selected,onSelect}) {
 const [query,setQuery]=useState(''),[actor,setActor]=useState(''),[feedMode,setFeedMode]=useState('authored'),[sort,setSort]=useState('newest');
 const [excluded,setExcluded]=useState([]),[group,setGroup]=useState(''),[ego,setEgo]=useState(null),[layout,setLayout]=useState('preset');
 const [labels,setLabels]=useState(true),[leftOpen,setLeftOpen]=useState(true),[rightOpen,setRightOpen]=useState(true),[detailTab,setDetailTab]=useState('detail');
 const [expanded,setExpanded]=useState(false);
 useEffect(()=>{if(!expanded)return;const close=e=>{if(e.key==='Escape')setExpanded(false);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[expanded]);
 useEffect(()=>{setExpanded(false);},[mode,frame]);
 const cyRef=useRef(null),host=useRef(null);
 const nodes=frame.nodes || [],edges=frame.edges || [],posts=frame.posts || [];
 const byId=useMemo(()=>Object.fromEntries([...nodes,...edges,...posts].map(n=>[n.id,n])),[frame]);
 const groups=useMemo(()=>[...new Set(nodes.filter(n=>n.kind!=='post').map(n=>n.group || '主体'))],[frame]);
 const allTypes=useMemo(()=>[...new Set(edges.map(e=>e.type))],[frame]);
 const types=allTypes.filter(t=>!excluded.includes(t));
 const graph=useMemo(()=>filterNetwork(frame,{types:allTypes.filter(t=>!excluded.includes(t)),group,ego}),[frame,excluded,group,ego]);
 const allActors=useMemo(()=>nodes.filter(n=>n.kind!=='post'),[frame]);
 const matchingActors=allActors;
 const list=useMemo(()=>filterPosts(frame,{actor,mode:feedMode,query,sort}),[frame,actor,feedMode,query,sort]);
 const objects=useMemo(()=>graph.nodes.filter(n=>`${n.label || n.id}`.toLowerCase().includes(query.toLowerCase())),[graph,query]);
 const current=byId[selected];
 const post=posts.find(p=>p.id===selected);
 const records=useMemo(()=>edges.filter(e=>e.source===selected || e.target===selected),[edges,selected]);
 const name=id=>byId[id]?.label || byId[id]?.title || id;
 const choose=id=>{onSelect(id);setRightOpen(true);setDetailTab('detail');};
 const near=(cy,id)=>{const el=cy.getElementById(id);const links=graph.edges.filter(e=>e.id===id || e.source===id || e.target===id);return cy.collection([el,...links.flatMap(e=>[cy.getElementById(e.id),cy.getElementById(e.source),cy.getElementById(e.target)])]);};
 const fit=()=>{const cy=cyRef.current;if(cy)cy.fit(cy.elements(':visible'),30);};
 const position=()=>{const cy=cyRef.current;if(!cy)return;if(layout==='preset')cy.nodes().positions(n=>n.data('home'));else cy.elements(':visible').layout({name:layout,animate:false,randomize:false,fit:false,nodeRepulsion:10000,idealEdgeLength:100}).run();fit();};
 useEffect(()=>{if(actor && !allActors.some(n=>n.id===actor))setActor('');if(selected && !byId[selected])onSelect(null);},[frame]);
 useEffect(()=>{if(mode==='posts'&&selected&&!list.some(p=>p.id===selected))onSelect(null);},[list,mode]);
 useEffect(()=>{
  if(mode!=='network')return;
  const colors=Object.fromEntries(groups.map((g,i)=>[g,palette[i%palette.length]]));
  const cy=cytoscape({container:host.current,minZoom:.08,maxZoom:4,elements:[...nodes.map((n,i)=>({data:{...n,label:n.label || n.title || n.id,color:n.color || colors[n.group || '主体'] || '#71839a',home:{x:n.x ?? 130+(i%6)*140,y:n.y ?? 100+Math.floor(i/6)*120}}})),...edges.map(e=>({data:{...e,label:typeNames[e.type] || e.type,color:palette[allTypes.indexOf(e.type)%palette.length]}}))],layout:{name:'preset'},style:[
   {selector:'node',style:{label:'data(label)','background-color':'data(color)',width:26,height:26,'font-size':12,'text-valign':'bottom','text-margin-y':7,color:'#344456','border-width':2,'border-color':'#fff'}},
   {selector:'node[kind="post"]',style:{shape:'round-rectangle','background-color':'#f5f7fa','border-color':'#899aaf','border-width':1.2}},
   {selector:'edge',style:{'curve-style':'bezier','line-color':'data(color)','target-arrow-color':'data(color)','target-arrow-shape':'triangle','arrow-scale':.8,width:1.5,opacity:.55}},
   {selector:'edge[type="follow"]',style:{'line-style':'dashed'}},
   {selector:'.dim',style:{opacity:.12,'text-opacity':.35}},
   {selector:'.selected',style:{'border-color':'#315cc8','border-width':4,'line-color':'#315cc8','target-arrow-color':'#315cc8',width:3,opacity:1}},
   {selector:'.hidden',style:{display:'none'}},
   {selector:'.no-label',style:{label:''}}
  ]});
  cyRef.current=cy;cy.on('tap','node,edge',e=>choose(e.target.id()));cy.on('tap',e=>{if(e.target===cy)onSelect(null);});
  const readable=()=>{const z=cy.zoom();cy.nodes().style({width:24/z,height:24/z,'font-size':12/z,'text-margin-y':7/z});};cy.on('zoom',readable);
  const resize=new ResizeObserver(()=>{cy.resize();cy.fit(cy.elements(':visible'),30);});resize.observe(host.current);
  return()=>{resize.disconnect();cy.destroy();cyRef.current=null;};
 },[frame,mode]);
 useEffect(()=>{const cy=cyRef.current;if(!cy)return;const ids=new Set([...graph.nodes,...graph.edges].map(n=>n.id));cy.batch(()=>cy.elements().forEach(e=>e.toggleClass('hidden',!ids.has(e.id()))));cy.elements().removeClass('dim selected');if(selected&&ids.has(selected)){cy.elements().difference(near(cy,selected)).addClass('dim');cy.getElementById(selected).addClass('selected');}},[graph,selected,mode]);
 useEffect(()=>{cyRef.current?.nodes().toggleClass('no-label',!labels);},[labels,mode,frame]);
 useEffect(()=>{position();},[layout,mode,frame]);
 const exportImage=()=>{const a=document.createElement('a');a.href=cyRef.current.png({bg:'#f8fafc',full:false,scale:2});a.download='society0-network.png';a.click();};
 const inspect=id=>{const node=nodes.find(n=>n.id===id);onInspect(node?.entityId || id);};
 const actorButton=n=>{const stats=actorActivity(frame,n.id);return <button className={actor===n.id?'active':''} onClick={()=>{setActor(n.id);onSelect(null);}}><span className="social-avatar">{(n.label || n.id).slice(0,1)}</span><span>{n.label || n.id}<small>{n.group || n.kind || '主体'}</small></span><span className="actor-stats"><span title="当前快照中的发帖数" aria-label={`发帖 ${stats.posts}`}><IconFileText size={11}/>{stats.posts}</span><span title={stats.likesGiven===null?'点赞主体资料未记录':'当前快照中点赞的帖子数'} aria-label={`点赞 ${stats.likesGiven??'未记录'}`}><IconHeart size={11}/>{stats.likesGiven??'—'}</span></span></button>;};
 return <section className={`social-workspace social-${mode} ${leftOpen?'':'left-closed'} ${rightOpen?'':'right-closed'}`}>
  {leftOpen&&<aside className="social-sidebar"><div className="social-heading">{mode==='posts'?<IconUsers size={14}/>:<IconStack2 size={14}/>}<strong>{mode==='posts'?'用户':'图层与对象'}</strong><Action label="收起左栏" Icon={IconLayoutSidebarLeftCollapse} onClick={()=>setLeftOpen(false)}/></div>
   {mode==='network'?<><div className="social-filters">{allTypes.map(t=><label key={t}><input type="checkbox" checked={types.includes(t)} onChange={()=>setExcluded(v=>v.includes(t)?v.filter(x=>x!==t):[...v,t])}/>{typeNames[t] || t}<small>{graph.edges.filter(e=>e.type===t).length}</small></label>)}<select aria-label="网络主体分组" value={group} onChange={e=>setGroup(e.target.value)}><option value="">全部分组</option>{groups.map(g=><option key={g}>{g}</option>)}</select></div><VirtualList items={objects} label="网络对象" render={n=><button className={selected===n.id?'active':''} onClick={()=>choose(n.id)}><span className="social-avatar">{n.kind==='post'?'帖':(n.label || n.id).slice(0,1)}</span><span>{n.label || n.id}<small>{n.group || n.kind}</small></span></button>}/></>:<><button className={!actor?'active':''} onClick={()=>{setActor('');onSelect(null);}}>全部用户 · {allActors.length}</button><VirtualList items={matchingActors} label="帖子作者" render={actorButton}/></>}
  </aside>}
  <main className="social-main" id="main-content"><div className="social-heading">
   {!leftOpen&&<Action label="展开左栏" Icon={IconLayoutSidebarLeftExpand} onClick={()=>setLeftOpen(true)}/>}
   <IconSearch size={14}/>
   <input aria-label={mode==='posts'?'搜索帖子':'搜索网络对象'} placeholder={mode==='posts'?'搜索正文或作者':'查找主体或帖子'} value={query} onChange={e=>setQuery(e.target.value)}/>
   {mode==='posts'?<><small>{list.length} 篇</small><select aria-label="帖子排序" value={sort} onChange={e=>setSort(e.target.value)}><option value="newest">最新发布</option><option value="oldest">最早发布</option><option value="replies">回复最多</option></select></>:<><small>{graph.nodes.length} 对象 · {graph.edges.length} 关系</small><Action label="导出网络图片" Icon={IconDownload} onClick={exportImage}/></>}
   {!rightOpen&&<Action label="展开详情栏" Icon={IconLayoutSidebarRightExpand} onClick={()=>setRightOpen(true)}/>}
  </div>
  {mode==='posts'?<>{actor&&<div className="social-scope"><strong>{name(actor)}</strong><button aria-pressed={feedMode==='authored'} onClick={()=>{setFeedMode('authored');onSelect(null);}}>发布的</button><button aria-pressed={feedMode==='feed'} onClick={()=>{setFeedMode('feed');onSelect(null);}}>推荐范围</button><button onClick={()=>inspect(actor)}>检查主体</button></div>}
   {actor&&feedMode==='feed'&&<p className="social-feed-note">这些帖子曾进入保存的推荐范围；正文与互动数为轮末状态。当时实际收到的内容请在“检查主体”的会话中查看。</p>}
   {actor&&feedMode==='feed'&&!Object.hasOwn(frame.feeds || {},actor)?<p className="social-empty">此时点尚未整理该用户的信息流资料。</p>:list.length?<VirtualList items={list} height={72} label="帖子列表" render={p=><button className={`post-preview ${selected===p.id?'active':''}`} onClick={()=>choose(p.id)}><span className="post-meta"><span className="post-author"><span className="social-avatar">{name(p.author).slice(0,1)}</span><b>{name(p.author)}</b></span><small>Tick {p.tick}</small><span className="post-stats"><span><IconHeart size={12}/>{p.likes===undefined?'未记录':Array.isArray(p.likes)?p.likes.length:p.likes}</span><span><IconMessageCircle size={12}/>{p.replies?.length || 0}</span></span></span><strong>{p.title || p.id}</strong><p>{p.content}</p></button>}/>:<p className="social-empty">当前范围没有帖子。可更换用户、搜索条件或观察时点。</p>}</>:<div className="social-canvas"><div ref={host} className="social-graph" role="img" aria-label="关系网络"/>{!graph.nodes.length&&<div className="social-empty">当前范围没有对象。<button onClick={()=>{setGroup('');setEgo(null);setExcluded([]);}}>返回全网</button></div>}
   <div className="social-tools"><Action label="放大" Icon={IconPlus} onClick={()=>cyRef.current.zoom(cyRef.current.zoom()*1.25)}/><Action label="缩小" Icon={IconMinus} onClick={()=>cyRef.current.zoom(cyRef.current.zoom()/1.25)}/><Action label="全图" Icon={IconFocus2} onClick={fit}/><Action label="定位选中对象" Icon={IconTarget} disabled={!current} onClick={()=>cyRef.current.fit(near(cyRef.current,selected),35)}/><select aria-label="网络布局" value={layout} onChange={e=>setLayout(e.target.value)}><option value="preset">分组布局</option><option value="cose">力导向</option><option value="circle">环形布局</option></select><Action label="重新排列" Icon={IconRotateClockwise} onClick={position}/><label><input type="checkbox" checked={labels} onChange={e=>setLabels(e.target.checked)}/>标签</label>{ego&&<button onClick={()=>setEgo(null)}>返回全网</button>}</div>
   <div className="social-legend">{groups.map((g,i)=><span key={g}><i style={{background:palette[i%palette.length]}}/>{g}</span>)}</div>
  </div>}
  </main>
  {rightOpen&&<aside className={`social-details ${expanded?'reading-expanded':''}`}><div className="social-heading"><IconFileText size={14}/><strong>{mode==='posts'?'帖子详情':'对象详情'}</strong><span className="detail-tools">{current&&<Action label={expanded?'收起详情阅读':'展开详情阅读'} Icon={expanded?IconX:IconArrowsMaximize} onClick={()=>setExpanded(!expanded)}/>}<Action label="收起详情" Icon={IconLayoutSidebarRightCollapse} onClick={()=>{setRightOpen(false);setExpanded(false);}}/></span></div>
   {current?<><div className="social-identity"><h2>{post?post.title || '帖子':current.source?`${name(current.source)} → ${name(current.target)}`:current.label || current.id}</h2><div className="identity-meta">{post&&<button onClick={()=>{setActor(post.author);setFeedMode('authored');onSelect(null);}}>{name(post.author)} · Tick {post.tick}</button>}<small title="记录 ID">{current.id}</small></div></div>
   <div className="social-detail-tabs"><button aria-pressed={detailTab==='detail'} onClick={()=>setDetailTab('detail')}>正文与详情</button><button aria-pressed={detailTab==='records'} onClick={()=>setDetailTab('records')}>关联记录 {records.length}</button></div>
   <div className="social-detail-body">{detailTab==='records'?records.map(e=><button className="social-record" key={e.id} onClick={()=>{onMode('network');choose(e.id);}}><small>{typeNames[e.type] || e.type} · {e.tick ?? '初始'}</small><b>{name(e.source)} → {name(e.target)}</b><p>{e.content || e.body}</p></button>):<><p className="post-full">{post?.content || current.description || current.content || current.body}</p>
    {post?<><div className="social-detail-actions"><button onClick={()=>{onMode('network');choose(post.id);}}><IconNetwork size={13}/>查看关系网络</button><button onClick={()=>inspect(post.author)}><IconUserSearch size={13}/>检查作者</button></div><details className="post-discussion" open><summary>回复 · {post.replies?.length || 0}</summary>{(post.replies || []).map(r=><article className="post-reply" key={r.id}><button onClick={()=>{setActor(r.author);setFeedMode('authored');onSelect(null);}}>{name(r.author)}</button><small>Tick {r.tick}</small><p>{r.content}</p></article>)}</details></>:current.source?<><p>{typeNames[current.type] || current.type} · Tick {current.tick ?? '初始'}</p><button onClick={()=>choose(current.source)}>查看来源</button><button onClick={()=>choose(current.target)}>查看目标</button></>:<><p>{current.group}</p><div className="social-detail-actions"><button onClick={()=>setEgo(ego===current.id?null:current.id)}>{ego?'返回全网':'一阶邻域'}</button><button onClick={()=>{setActor(current.id);setFeedMode('authored');onMode('posts');onSelect(null);}}>查看帖子</button><button onClick={()=>inspect(current.id)}>检查主体</button></div></>}
    <details className="social-source"><summary>原始记录与来源</summary><p>{current.sourceRef || frame.source || '来源未填写'}</p><pre>{JSON.stringify(current,null,2)}</pre></details>
   </>}</div></>:<p className="social-empty">{mode==='posts'?'选择一篇帖子，查看全文、回复和关联关系。':'选择节点、连线或左侧对象，查看详情。'}</p>}
  </aside>}
 </section>;
}
