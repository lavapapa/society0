import React, { useMemo, useState } from 'react';
import { formatTime } from './data.js';
import { StateBlock } from './ui.jsx';

/* ---------- 空间图：抽象平面与地理坐标共用 ---------- */

function project(points, width, height, pad = 28) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  return (point) => ({
    cx: pad + ((point.x - minX) / spanX) * (width - pad * 2),
    cy: height - pad - ((point.y - minY) / spanY) * (height - pad * 2),
  });
}

export function MapPanel({ dataset }) {
  const nodes = dataset?.nodes || [];
  const routes = dataset?.routes || [];
  const missing = dataset?.missing || [];
  const [focus, setFocus] = useState('');
  const width = 620;
  const height = 320;
  const located = nodes.filter((node) => typeof node.x === 'number' && typeof node.y === 'number');
  const projectPoint = useMemo(() => (located.length ? project(located, width, height) : null), [located]);

  if (!nodes.length) return <StateBlock kind="empty" title="没有空间资料" tight>坐标或区域随资料进入。</StateBlock>;

  return (
    <div className="map">
      <div className="map-tools">
        <span>{located.length} / {nodes.length} 个对象有坐标</span>
        {missing.length ? <span className="map-missing">缺坐标：{missing.map((item) => item.label).join('、')}</span> : null}
        {focus ? <button type="button" className="link-button" onClick={() => setFocus('')}>清除选择</button> : null}
      </div>
      <div className="map-stage">
        {projectPoint ? (
          <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={dataset.title || '空间分布'} style={{ maxHeight: 340 }}>
            <rect x="0" y="0" width={width} height={height} fill="#f8fbfd" />
            {[0.25, 0.5, 0.75].map((ratio) => (
              <line key={ratio} x1={width * ratio} x2={width * ratio} y1="0" y2={height} stroke="#eef3f7" />
            ))}
            {routes.map((route, index) => {
              const from = located.find((node) => node.id === route.from);
              const to = located.find((node) => node.id === route.to);
              if (!from || !to) return null;
              const a = projectPoint(from);
              const b = projectPoint(to);
              return (
                <g key={route.id || index}>
                  <line x1={a.cx} y1={a.cy} x2={b.cx} y2={b.cy} stroke={route.failed ? '#c98b84' : '#a9c4d6'} strokeWidth={route.weight ? Math.min(4, 1 + route.weight) : 1.4} strokeDasharray={route.interrupted ? '4 4' : undefined} />
                  {route.label ? <text x={(a.cx + b.cx) / 2} y={(a.cy + b.cy) / 2 - 4} className="route-label">{route.label}</text> : null}
                </g>
              );
            })}
            {located.map((node) => {
              const point = projectPoint(node);
              const active = focus === node.id;
              return (
                <g
                  key={node.id}
                  transform={`translate(${point.cx},${point.cy})`}
                  className={`map-node${active ? ' active' : ''}`}
                  tabIndex={0}
                  role="button"
                  aria-label={node.label}
                  onClick={() => setFocus(node.id)}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setFocus(node.id); } }}
                >
                  <circle r={node.size ? 4 + node.size : 6} />
                  <text y={-10} className="map-label">{node.label}</text>
                  {node.note ? <text y={16} className="map-note">{node.note}</text> : null}
                </g>
              );
            })}
          </svg>
        ) : (
          <StateBlock kind="awaiting" title="对象还没有坐标" tight>名录与关系仍可阅读，地图需要位置资料。</StateBlock>
        )}
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}。抽象平面按相对位置投影，不代表真实地理比例。</p> : null}
    </div>
  );
}

/* ---------- 轨迹 ---------- */

export function TrackPanel({ dataset }) {
  const tracks = dataset?.tracks || [];
  if (!tracks.length) return <StateBlock kind="empty" title="没有轨迹资料" tight>逐时点位置随资料进入。</StateBlock>;
  const width = 620;
  const height = 280;
  const all = tracks.flatMap((track) => track.points || []);
  const projectPoint = project(all, width, height);
  const [index, setIndex] = useState(all.length - 1);
  const current = all[index];
  const owner = tracks.find((track) => (track.points || []).includes(current));

  return (
    <div className="track">
      <div className="track-controls">
        <input type="range" min="0" max={all.length - 1} value={index} aria-label="轨迹时点" onChange={(event) => setIndex(Number(event.target.value))} />
        <span>{current ? formatTime(current.t) : '—'}</span>
      </div>
      <ul className="track-legend">
        {tracks.map((track) => (
          <li key={track.id} className={owner?.id === track.id ? 'active' : ''}><i />{track.label || track.id} · {track.points?.length || 0} 个位置</li>
        ))}
        {current && owner ? <li className="track-current">当前：{owner.label || owner.id} · {current.x}, {current.y}</li> : null}
      </ul>
      <div className="map-stage">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="轨迹" style={{ maxHeight: 260 }}>
          <rect x="0" y="0" width={width} height={height} fill="#f8fbfd" />
          {tracks.map((track, trackIndex) => {
            const pts = (track.points || []).map((point) => {
              const p = projectPoint(point);
              return `${p.cx.toFixed(1)},${p.cy.toFixed(1)}`;
            }).join(' ');
            return <polyline key={track.id || trackIndex} points={pts} fill="none" stroke="#a9c4d6" strokeWidth="1.6" />;
          })}
          {current ? (() => {
            const point = projectPoint(current);
            return <circle cx={point.cx} cy={point.cy} r="5" fill="#4a8fc2" stroke="#fff" strokeWidth="1.5" />;
          })() : null}
        </svg>
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- OD 矩阵 ---------- */

export function ODPanel({ dataset }) {
  const rows = dataset?.rows || [];
  const columns = dataset?.columns || [];
  const matrix = dataset?.matrix || [];
  if (!rows.length) return <StateBlock kind="empty" title="没有 OD 资料" tight>起讫矩阵随资料进入。</StateBlock>;
  const values = matrix.flat();
  const max = Math.max(...values, 1);
  return (
    <div className="od">
      <table className="od-table">
        <thead>
          <tr><th>起点 \ 终点</th>{columns.map((col) => <th key={col.id}>{col.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={row.id}>
              <th scope="row">{row.label}</th>
              {columns.map((col, colIndex) => {
                const value = matrix[rowIndex]?.[colIndex];
                const ratio = typeof value === 'number' ? value / max : null;
                return (
                  <td key={col.id} style={{ background: ratio === null ? '#fafbfc' : `rgba(74,143,194,${0.06 + ratio * 0.5})`, color: ratio !== null && ratio > 0.6 ? '#fff' : '#365b8f' }}>
                    {typeof value === 'number' ? value.toLocaleString('zh-CN') : '—'}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 扩散状态 ---------- */

export function DiffusionPanel({ dataset }) {
  const states = dataset?.states || [];
  const timeline = dataset?.timeline || [];
  if (!states.length) return <StateBlock kind="empty" title="没有扩散资料" tight>状态定义与群体曲线随资料进入。</StateBlock>;
  return (
    <div className="diffusion">
      <div className="state-legend">
        {states.map((state) => (
          <span key={state.id}><i style={{ background: state.color }} />{state.label}</span>
        ))}
      </div>
      <div className="state-bars">
        {states.map((state) => (
          <div className="state-bar" key={state.id}>
            <span>{state.label}</span>
            <div className="state-track"><div style={{ width: `${state.share * 100}%`, background: state.color }} /></div>
            <span>{Math.round(state.share * 100)}%</span>
          </div>
        ))}
      </div>
      {timeline.length ? (
        <table className="diffusion-table">
          <thead><tr><th>时点</th>{states.map((state) => <th key={state.id}>{state.label}</th>)}</tr></thead>
          <tbody>
            {timeline.map((row) => (
              <tr key={row.t}><td>{formatTime(row.t)}</td>{states.map((state) => <td key={state.id}>{row[state.id] ?? '—'}</td>)}</tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}
