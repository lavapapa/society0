import React, { useEffect, useMemo, useRef, useState } from 'react';
import { formatTime } from './data.js';
import { Badge, StateBlock, Skeleton } from './ui.jsx';

/* ---------- 面板网格：尺寸预设 + 排序 + 键盘移动，持久在浏览器 ---------- */

const SIZES = {
  s: { label: '小', span: 4 },
  m: { label: '中', span: 6 },
  l: { label: '大', span: 8 },
  full: { label: '整行', span: 12 },
};

export function PanelGrid({ panels, renderPanel, layout, onLayout }) {
  const order = layout?.order?.length ? layout.order.filter((id) => panels.some((p) => p.id === id)) : panels.map((p) => p.id);
  const missing = panels.filter((p) => !order.includes(p.id)).map((p) => p.id);
  const sequence = [...order, ...missing];
  const sizes = layout?.sizes || {};
  const byId = new Map(panels.map((p) => [p.id, p]));

  if (!panels.length) {
    return (
      <StateBlock kind="unconfigured" title="还没有可呈现的面板">
        资料里还没有声明面板。可以先和 Agent 讨论这次实验要回答什么，再决定用哪些组件。
      </StateBlock>
    );
  }

  const move = (id, delta) => {
    onLayout((current) => {
      const currentLayout = current || {};
      const currentOrder = currentLayout.order?.length ? currentLayout.order.filter((item) => byId.has(item)) : panels.map((item) => item.id);
      const order = [...currentOrder, ...panels.map((item) => item.id).filter((item) => !currentOrder.includes(item))];
      const index = order.indexOf(id);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= order.length) return currentLayout;
      [order[index], order[target]] = [order[target], order[index]];
      return { ...currentLayout, order };
    });
  };

  const resize = (id, size) => {
    onLayout((current) => ({ ...(current || {}), sizes: { ...(current?.sizes || {}), [id]: size } }));
  };

  return (
    <div className="panel-grid">
      {sequence.map((id, index) => {
        const panel = byId.get(id);
        if (!panel) return null;
        const size = sizes[id] || panel.size || 'm';
        return (
          <section
            key={id}
            className={`panel-tile span-${size}`}
            aria-label={panel.title}
            onKeyDown={(event) => {
              if (event.altKey && event.key === 'ArrowUp') { event.preventDefault(); move(id, -1); }
              if (event.altKey && event.key === 'ArrowDown') { event.preventDefault(); move(id, 1); }
            }}
            tabIndex={0}
          >
            <header className="tile-head">
              <h3>{panel.title}</h3>
              {panel.note ? <small>{panel.note}</small> : null}
              <span className="spacer" />
              <div className="tile-size" role="group" aria-label={`${panel.title}尺寸`}>
                {Object.entries(SIZES).map(([key, value]) => (
                  <button key={key} type="button" aria-pressed={size === key} onClick={() => resize(id, key)} title={value.label}>{key === 'full' ? '全' : key}</button>
                ))}
              </div>
              <button type="button" className="icon-button" aria-label="上移面板" disabled={index === 0} onClick={() => move(id, -1)}>↑</button>
              <button type="button" className="icon-button" aria-label="下移面板" disabled={index === sequence.length - 1} onClick={() => move(id, 1)}>↓</button>
            </header>
            <div className="tile-body">{renderPanel(panel)}</div>
          </section>
        );
      })}
    </div>
  );
}

/* ---------- 数据状态包装 ---------- */

function DatasetState({ dataset, children }) {
  const state = dataset?.availability || 'ready';
  if (!dataset) return <StateBlock kind="unconfigured" title="数据未绑定" tight>这个面板还没有对应的资料。</StateBlock>;
  if (state === 'loading') return <Skeleton lines={4} />;
  if (state === 'unconfigured') return <StateBlock kind="unconfigured" title="尚未配置" tight>{dataset.note || '这块资料还没有确定内容。'}</StateBlock>;
  if (state === 'awaiting') return <StateBlock kind="awaiting" title="资料待提供" tight>{dataset.note || '资料更新后会出现在这里。'}</StateBlock>;
  if (state === 'empty') return <StateBlock kind="empty" title="这个范围没有记录" tight>{dataset.note || '保留当前筛选条件，可以稍后刷新。'}</StateBlock>;
  if (state === 'error') return <StateBlock kind="error" title="读取失败" tight>{dataset.note || '资料格式或引用有问题。'}</StateBlock>;
  return children;
}

/* ---------- 指标 ---------- */

export function MetricPanel({ dataset }) {
  return (
    <DatasetState dataset={dataset}>
      <div className="metric-grid">
        {(dataset.items || []).map((item) => (
          <div className="metric" key={item.id || item.label}>
            <span>{item.label}</span>
            <strong>{item.value ?? '—'}{item.unit ? <small>{item.unit}</small> : null}</strong>
            {item.delta ? <em className={item.deltaTone || ''}>{item.delta}</em> : null}
            {item.note ? <small className="metric-note">{item.note}</small> : null}
          </div>
        ))}
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </DatasetState>
  );
}

/* ---------- 表格 ---------- */

export function TablePanel({ dataset }) {
  const [filter, setFilter] = useState('');
  const rows = dataset?.rows || [];
  const columns = dataset?.columns || [];
  const shown = useMemo(() => {
    if (!filter.trim()) return rows;
    const text = filter.trim().toLowerCase();
    return rows.filter((row) => columns.some((col) => String(row[col.key] ?? '').toLowerCase().includes(text)));
  }, [rows, columns, filter]);

  return (
    <DatasetState dataset={dataset}>
      <div className="table-tools">
        <input type="search" value={filter} placeholder="筛选本表" aria-label="筛选表格" onChange={(event) => setFilter(event.target.value)} />
        <small>{shown.length} / {dataset.total ?? rows.length} 行{dataset.loadedRange ? `（已载入 ${dataset.loadedRange}）` : ''}</small>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>{columns.map((col) => <th key={col.key} style={{ textAlign: col.align || 'left' }}>{col.label}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((row, index) => (
              <tr key={row.id || index}>
                {columns.map((col) => <td key={col.key} style={{ textAlign: col.align || 'left' }}>{row[col.key] ?? '—'}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {!shown.length ? <p className="table-empty">没有符合条件的记录。</p> : null}
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </DatasetState>
  );
}

/* ---------- 时序 ---------- */

export function SeriesPanel({ dataset }) {
  const series = dataset?.series || [];
  const points = series.flatMap((line) => line.points || []);
  if (!points.length) {
    return <StateBlock kind="empty" title="序列还没有数据点" tight>资料提供后会自动绘制。</StateBlock>;
  }
  const width = 560;
  const height = 190;
  const pad = { left: 34, right: 44, top: 14, bottom: 22 };
  const xs = points.map((point) => point.t);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (value) => pad.left + (maxX === minX ? plotW / 2 : ((value - minX) / (maxX - minX)) * plotW);
  const colors = ['#4a8fc2', '#bf8755', '#7fb09a', '#8c7fb0', '#c98b84'];

  return (
    <DatasetState dataset={dataset}>
      <div className="series">
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={dataset.title || '时序图'} style={{ height }}>
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
            <line key={ratio} x1={pad.left} x2={width - pad.right} y1={pad.top + plotH * ratio} y2={pad.top + plotH * ratio} stroke="#eef3f7" />
          ))}
          <line x1={pad.left} x2={pad.left} y1={pad.top} y2={pad.top + plotH} stroke="#dfe7ee" />
          {series.map((line, index) => {
            const values = (line.points || []).map((point) => point.v);
            const lo = Math.min(...values);
            const hi = Math.max(...values);
            const y = (value) => pad.top + plotH - (hi === lo ? plotH / 2 : ((value - lo) / (hi - lo)) * (plotH - 6) + 3);
            const path = (line.points || []).map((point) => `${x(point.t).toFixed(1)},${y(point.v).toFixed(1)}`).join(' ');
            const last = line.points?.at(-1);
            return (
              <g key={line.key || index}>
                <polyline points={path} fill="none" stroke={colors[index % colors.length]} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
                {last ? (
                  <text x={width - pad.right + 6} y={y(last.v) + 3} fontSize="9" fill={colors[index % colors.length]}>
                    {formatNumber(last.v)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        <ul className="series-legend">
          {series.map((line, index) => (
            <li key={line.key || index}>
              <i style={{ background: colors[index % colors.length] }} />
              {line.label || line.key}{line.unit ? ` · ${line.unit}` : ''}
              <span className="legend-range">
                {formatNumber(Math.min(...(line.points || []).map((p) => p.v)))}–{formatNumber(Math.max(...(line.points || []).map((p) => p.v)))}
              </span>
            </li>
          ))}
        </ul>
      </div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}{dataset.range ? ` · 范围 ${dataset.range}` : ''}</p> : null}
    </DatasetState>
  );
}

function formatNumber(value) {
  if (value == null) return '—';
  const abs = Math.abs(value);
  if (abs >= 100000000) return `${(value / 100000000).toFixed(2)} 亿`;
  if (abs >= 10000) return `${(value / 10000).toFixed(2)} 万`;
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/* ---------- 记录浏览与会话 ---------- */

export function RecordPanel({ dataset, selection, select }) {
  const records = dataset?.records || [];
  const [active, setActive] = useState(dataset?.defaultRecordId || records[0]?.id || '');
  const current = records.find((item) => item.id === active) || null;
  return (
    <DatasetState dataset={dataset}>
      {records.length ? (
        <div className="record-split">
          <ul className="record-list">
            {records.map((item) => (
              <li key={item.id}>
                <button type="button" className="record-item" aria-pressed={active === item.id} onClick={() => setActive(item.id)}>
                  <strong>{item.title || item.label || item.id}</strong>
                  <small>{formatTime(item.at)}{item.actor ? ` · ${item.actor}` : ''}</small>
                </button>
              </li>
            ))}
          </ul>
          <div className="record-detail">
            {current ? (
              <>
                <div className="record-detail-head">
                  <strong>{current.title}</strong>
                  <Badge state={current.availability}>{current.availability === 'partial' ? '部分资料' : '原文'}</Badge>
                </div>
                {(current.body || '').split('\n').filter(Boolean).map((line, index) => <p key={index}>{line}</p>)}
                {current.entityId ? (
                  <button type="button" className="link-button" onClick={() => select({ entityId: current.entityId, pageId: 'setup' })}>查看相关主体</button>
                ) : null}
              </>
            ) : <p className="muted">选择一条记录查看原文。</p>}
          </div>
        </div>
      ) : <StateBlock kind="empty" title="没有记录" tight>当前范围没有可读条目。</StateBlock>}
    </DatasetState>
  );
}

export function ThreadPanel({ dataset }) {
  const messages = dataset?.messages || [];
  return (
    <DatasetState dataset={dataset}>
      <ol className="thread">
        {messages.map((message) => (
          <li key={message.seq} className={`thread-item ${message.role || 'tool'}`}>
            <div className="thread-meta">
              <span className="thread-role">{roleLabel(message.role)}</span>
              <span className="mono">#{message.seq}</span>
              {message.tool ? <span className="mono">{message.tool}</span> : null}
              {message.status ? <Badge state={message.status === 'failed' ? 'error' : undefined}>{message.status}</Badge> : null}
            </div>
            <p>{message.content}</p>
          </li>
        ))}
      </ol>
      {dataset.total > messages.length ? (
        <p className="panel-source">已显示 {messages.length} / {dataset.total} 条，其余可在资料更新后继续读取。</p>
      ) : null}
    </DatasetState>
  );
}

function roleLabel(role) {
  return { system: '系统', user: '输入', assistant: '模型输出', tool: '工具结果' }[role] || '记录';
}

/* ---------- 文档 ---------- */

export function DocumentPanel({ dataset }) {
  return (
    <DatasetState dataset={dataset}>
      <article className="doc">
        {(dataset.paragraphs || []).map((text, index) => <p key={index}>{text}</p>)}
        {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
      </article>
    </DatasetState>
  );
}

/* ---------- 注册与分发 ---------- */

export const PANEL_TYPES = {
  metric: MetricPanel,
  table: TablePanel,
  series: SeriesPanel,
  records: RecordPanel,
  thread: ThreadPanel,
  document: DocumentPanel,
};

export function renderPanel(panel, datasets, selection, select) {
  const dataset = datasets.find((item) => item.id === panel.datasetId) || null;
  if (panel.type === 'custom' && typeof window.SOCIETY0_PANELS?.[panel.renderer] === 'function') {
    const Custom = window.SOCIETY0_PANELS[panel.renderer];
    return <Custom dataset={dataset} selection={selection} select={select} />;
  }
  const Component = PANEL_TYPES[panel.type] || PANEL_TYPES.document;
  if (panel.type === 'records') return <Component dataset={dataset} selection={selection} select={select} />;
  return <Component dataset={dataset} />;
}
