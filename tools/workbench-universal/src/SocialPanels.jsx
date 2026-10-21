import React, { useMemo, useRef, useState } from 'react';
import { formatTime } from './data.js';
import { Badge, StateBlock } from './ui.jsx';

/* ---------- 网络图：确定性分层布局，不引入图库 ---------- */

function layoutNodes(nodes, edges) {
  const groups = new Map();
  for (const node of nodes) {
    const key = node.group || '未分组';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  }
  const width = 720;
  const rowHeight = 132;
  const height = Math.max(240, groups.size * rowHeight + 30);
  const positions = new Map();
  [...groups.entries()].forEach(([group, items], groupIndex) => {
    const columns = Math.min(3, Math.max(1, items.length));
    const rows = Math.ceil(items.length / columns);
    items.forEach((node, index) => {
      const row = Math.floor(index / columns);
      const column = index % columns;
      const offset = row % 2 === 1 ? 40 : 0;
      const x = columns === 1 ? width / 2 : 130 + offset + (column * (width - 260)) / Math.max(1, columns - 1);
      positions.set(node.id, { x, y: 54 + groupIndex * rowHeight + row * 52, group });
    });
    if (rows > 1) return;
  });
  return { positions, width, height, groups: [...groups.keys()] };
}

export function GraphPanel({ dataset, selection, select }) {
  const nodes = dataset?.nodes || [];
  const edges = dataset?.edges || [];
  const [focus, setFocus] = useState(dataset?.defaultFocus || '');
  const [layer, setLayer] = useState('');
  const [hover, setHover] = useState('');

  const layers = useMemo(() => [...new Set(edges.map((edge) => edge.relation || '关系'))], [edges]);
  const visibleEdges = layer ? edges.filter((edge) => (edge.relation || '关系') === layer) : edges;
  const neighbors = useMemo(() => {
    const map = new Map();
    for (const edge of visibleEdges) {
      if (!map.has(edge.source)) map.set(edge.source, new Set());
      if (!map.has(edge.target)) map.set(edge.target, new Set());
      map.get(edge.source).add(edge.target);
      map.get(edge.target).add(edge.source);
    }
    return map;
  }, [visibleEdges]);
  const highlighted = new Set([focus, ...(neighbors.get(focus) || [])].filter(Boolean));

  const { positions, width, height, groups } = useMemo(() => layoutNodes(nodes, visibleEdges), [nodes, visibleEdges]);

  if (!nodes.length) {
    return <StateBlock kind="empty" title="还没有网络资料" tight>节点与关系随资料进入。</StateBlock>;
  }

  return (
    <div className="graph">
      <div className="graph-tools">
        <input type="search" placeholder="查找节点" aria-label="查找节点" onChange={(event) => setFocus(nodes.find((n) => n.label.includes(event.target.value))?.id || '')} />
        <select aria-label="关系图层" value={layer} onChange={(event) => setLayer(event.target.value)}>
          <option value="">全部关系</option>
          {layers.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <small>{nodes.length} 节点 · {visibleEdges.length} 关系</small>
        <button type="button" onClick={() => setFocus('')} disabled={!focus}>清除聚焦</button>
      </div>

      <div className="graph-stage">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={dataset.title || '关系网络'}>
          {groups.map((group, index) => (
            <g key={group}>
              <text x="12" y={56 + index * 118 - 30} className="group-label">{group}</text>
              <line x1="12" x2={width - 12} y1={56 + index * 118 - 22} y2={56 + index * 118 - 22} stroke="#eef3f7" />
            </g>
          ))}
          {visibleEdges.map((edge, index) => {
            const from = positions.get(edge.source);
            const to = positions.get(edge.target);
            if (!from || !to) return null;
            const dim = focus && !(highlighted.has(edge.source) && highlighted.has(edge.target));
            return (
              <line
                key={`${edge.source}-${edge.target}-${index}`}
                x1={from.x} y1={from.y} x2={to.x} y2={to.y}
                stroke={dim ? '#eef3f7' : '#b9cfdd'}
                strokeWidth={edge.weight ? Math.min(3, 0.8 + edge.weight * 0.6) : 1}
                strokeDasharray={edge.dashed ? '3 3' : undefined}
              />
            );
          })}
          {nodes.map((node) => {
            const position = positions.get(node.id);
            if (!position) return null;
            const active = focus === node.id;
            const related = highlighted.has(node.id);
            return (
              <g
                key={node.id}
                transform={`translate(${position.x},${position.y})`}
                className={`node${active ? ' active' : ''}${focus && !related ? ' dim' : ''}`}
                tabIndex={0}
                role="button"
                aria-label={`${node.label}${node.kind ? `，${node.kind}` : ''}`}
                onMouseEnter={() => setHover(node.id)}
                onMouseLeave={() => setHover('')}
                onClick={() => { setFocus(node.id); if (node.entityId) select({ entityId: node.entityId, pageId: 'setup' }); }}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setFocus(node.id); } }}
              >
                <circle r={node.size ? 7 + Math.min(8, node.size) : 9} />
                <text y={22} className="node-label">{node.label}</text>
                {(hover === node.id || active) && node.note ? <text y={33} className="node-note">{node.note}</text> : null}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="graph-legend">
        {layers.map((item) => <span key={item}><i />{item}</span>)}
        {focus ? <button type="button" className="link-button" onClick={() => select({ pageId: 'observe' })}>相关记录</button> : null}
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 帖子浏览 ---------- */

export function FeedPanel({ dataset, selection, select }) {
  const posts = dataset?.posts || [];
  const [active, setActive] = useState(dataset?.defaultPostId || posts[0]?.id || '');
  const [sort, setSort] = useState('original');
  const [scope, setScope] = useState('all');
  const current = posts.find((post) => post.id === active) || null;

  const sorted = useMemo(() => {
    const items = posts.filter((post) => scope === 'all' || post.scope === scope);
    if (sort === 'latest') return [...items].sort((a, b) => String(b.at).localeCompare(String(a.at)));
    if (sort === 'replies') return [...items].sort((a, b) => (b.replies?.length || 0) - (a.replies?.length || 0));
    return items;
  }, [posts, sort, scope]);

  if (!posts.length) {
    return <StateBlock kind="empty" title="还没有帖子" tight>帖子、回复与互动随资料进入。</StateBlock>;
  }

  return (
    <div className="feed">
      <div className="feed-tools">
        <select aria-label="帖子排序" value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="original">原推荐顺序</option>
          <option value="latest">最新发布</option>
          <option value="replies">回复最多</option>
        </select>
        <select aria-label="帖子范围" value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="all">全部范围</option>
          <option value="recommended">推荐范围</option>
          <option value="visible">实际可见</option>
        </select>
        <small>{sorted.length} 篇</small>
      </div>
      <div className="feed-split">
        <ul className="feed-list">
          {sorted.map((post) => (
            <li key={post.id}>
              <button type="button" className="feed-item" aria-pressed={active === post.id} onClick={() => setActive(post.id)}>
                <div className="feed-meta"><span>{post.author}</span><small>{formatTime(post.at)}</small></div>
                <p>{post.content}</p>
                <div className="feed-stats">
                  <span>{post.replies?.length || 0} 回复</span>
                  <span>{post.likes || 0} 赞</span>
                  {post.scope === 'recommended' ? <Badge>推荐</Badge> : null}
                  {post.scope === 'visible' ? <Badge state="ready">已见</Badge> : null}
                </div>
              </button>
            </li>
          ))}
        </ul>
        <div className="feed-detail">
          {current ? (
            <>
              <div className="feed-detail-head">
                <div>
                  <strong>{current.author}</strong>
                  <small>{formatTime(current.at)}{current.tick ? ` · 第 ${current.tick} 轮` : ''}</small>
                </div>
                {current.authorEntityId ? <button type="button" className="link-button" onClick={() => select({ entityId: current.authorEntityId, pageId: 'setup' })}>查看作者</button> : null}
              </div>
              <p className="feed-body">{current.content}</p>
              {current.replies?.length ? (
                <div className="feed-replies">
                  <strong style={{ fontSize: 11 }}>回复 · {current.replies.length}</strong>
                  {current.replies.map((reply) => (
                    <div key={reply.id} className="reply">
                      <div className="feed-meta"><span>{reply.author}</span><small>{formatTime(reply.at)}</small></div>
                      <p>{reply.content}</p>
                    </div>
                  ))}
                </div>
              ) : <p className="muted" style={{ fontSize: 10.5 }}>还没有回复。</p>}
              {current.originalOrder !== undefined ? <p className="panel-source">原推荐位次：第 {current.originalOrder + 1} 位</p> : null}
            </>
          ) : null}
        </div>
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 曝光与注入检查 ---------- */

export function ExposurePanel({ dataset }) {
  if (!dataset) return <StateBlock kind="unconfigured" title="没有曝光资料" tight>需要推荐、可见与实际注入三组记录。</StateBlock>;
  const rows = dataset.rows || [];
  return (
    <div className="exposure">
      <table className="exposure-table">
        <thead><tr><th>主体</th><th>推荐池</th><th>实际可见</th><th>注入模型</th><th>轮末新增</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.actor}</td>
              <td>{row.recommended ?? '—'}</td>
              <td>{row.visible ?? '—'}</td>
              <td>{row.injected ?? '未记录'}</td>
              <td>{row.addedLater ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="panel-source">三列分别代表候选、实际可见与模型输入；轮末新增不补算为当时已见。{dataset.source ? ` 来源：${dataset.source}` : ''}</p>
    </div>
  );
}
