import React, { useEffect, useRef, useState } from "react";
import { visibleRows } from "./model";

function EmptyView({ children = "这个呈现方式尚无资料。" }) {
  return <p className="view-empty">{children}</p>;
}

function Candlestick({ view }) {
  const allRows = view.rows || [];
  const rows = allRows.slice(-Math.max(1, view.maxPoints || 500));
  if (!rows.length) return <EmptyView />;
  if (rows.some((row) => ![row.open, row.high, row.low, row.close].every(Number.isFinite))) {
    return <EmptyView>K 线需要每个时点都有数值型的开、高、低、收。</EmptyView>;
  }
  let low = Infinity;
  let high = -Infinity;
  for (const row of rows) {
    for (const value of [row.open, row.high, row.low, row.close]) {
      if (Number.isFinite(value)) { low = Math.min(low, value); high = Math.max(high, value); }
    }
  }
  if (!Number.isFinite(low)) return <EmptyView>缺少有效的开、高、低、收数据。</EmptyView>;
  const span = high - low || 1;
  const width = 700;
  const height = 260;
  const left = 52;
  const right = 18;
  const top = 18;
  const bottom = 224;
  const plotWidth = width - left - right;
  const x = (index) => left + (index + .5) * plotWidth / rows.length;
  const y = (value) => top + (high - value) / span * (bottom - top);
  const candleWidth = Math.max(2, Math.min(16, plotWidth / rows.length * .6));
  const labels = [0, .25, .5, .75, 1].map((share) => low + span * share);
  return (
    <div className="chart-wrap">
      <svg className="data-chart" viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={view.ariaLabel || `${view.title || "K 线"}，共 ${rows.length} 个时点`}>
        {labels.map((value, index) => <g key={index}>
          <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke="#e6edf2" />
          <text x={left - 9} y={y(value) + 4} textAnchor="end">{value.toFixed(view.precision ?? 1)}</text>
        </g>)}
        {rows.map((row, index) => {
          const rise = row.close >= row.open;
          const color = rise ? "#bd7168" : "#4b9b89";
          return <g key={row.x ?? index}>
            <line x1={x(index)} x2={x(index)} y1={y(row.high)} y2={y(row.low)} stroke={color} />
            <rect x={x(index) - candleWidth / 2} y={Math.min(y(row.open), y(row.close))}
              width={candleWidth} height={Math.max(2, Math.abs(y(row.open) - y(row.close)))} fill={color} />
            <title>{`${row.x ?? index}：开 ${row.open}，高 ${row.high}，低 ${row.low}，收 ${row.close}`}</title>
          </g>;
        })}
        <text x={left} y={height - 10}>{rows[0].x}</text>
        <text x={width - right} y={height - 10} textAnchor="end">{rows.at(-1).x}</text>
      </svg>
      {(view.unit || rows.length < allRows.length) && <p className="view-caption">
        {view.unit && `单位：${view.unit}。`}
        {rows.length < allRows.length && `展示最近 ${rows.length} / ${allRows.length} 个时点。`}
      </p>}
    </div>
  );
}

function TimeSeries({ view }) {
  const allRows = view.rows || [];
  const rows = allRows.slice(-Math.max(1, view.maxPoints || 500));
  if (rows.some((row) => !Number.isFinite(row.y))) return <EmptyView>时间序列需要每个时点都有数值。</EmptyView>;
  const values = rows.map((row) => Number(row.y)).filter(Number.isFinite);
  if (!values.length) return <EmptyView />;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const width = 700;
  const height = 230;
  const left = 45;
  const right = 20;
  const top = 18;
  const bottom = 195;
  const x = (index) => left + index / Math.max(1, rows.length - 1) * (width - left - right);
  const y = (value) => top + (high - value) / span * (bottom - top);
  const path = rows.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(row.y)}`).join(" ");
  return <div className="chart-wrap">
    <svg className="data-chart" viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label={view.ariaLabel || `${view.title || "时间序列"}，共 ${rows.length} 个时点`}>
      <line x1={left} x2={width - right} y1={bottom} y2={bottom} stroke="#dbe5ed" />
      <path d={path} fill="none" stroke="#568ec2" strokeWidth="2.5" />
      {rows.map((row, index) => <circle key={row.x ?? index} cx={x(index)} cy={y(row.y)} r="3" fill="#568ec2">
        <title>{`${row.x ?? index}：${row.y}`}</title>
      </circle>)}
      <text x={left} y={height - 6}>{rows[0].x}</text>
      <text x={width - right} y={height - 6} textAnchor="end">{rows.at(-1).x}</text>
    </svg>
    {(view.unit || rows.length < allRows.length) && <p className="view-caption">
      {view.unit && `单位：${view.unit}。`}
      {rows.length < allRows.length && `展示最近 ${rows.length} / ${allRows.length} 个时点。`}
    </p>}
  </div>;
}

function Network({ view }) {
  const nodes = view.nodes || [];
  const edges = view.edges || [];
  const [selected, setSelected] = useState(null);
  if (!nodes.length) return <EmptyView />;
  const positioned = nodes.map((node, index) => {
    const angle = index / nodes.length * Math.PI * 2 - Math.PI / 2;
    return { ...node, px: node.x == null ? 50 + 37 * Math.cos(angle) : node.x * 100,
      py: node.y == null ? 50 + 37 * Math.sin(angle) : node.y * 100 };
  });
  const byId = Object.fromEntries(positioned.map((node) => [node.id, node]));
  const active = byId[selected];
  return <>
    <svg className="network-chart" viewBox="0 0 100 100" role="img"
      aria-label={view.ariaLabel || `关系网络，${nodes.length} 个节点，${edges.length} 条关系`}>
      {edges.map((edge, index) => {
        const from = byId[edge.from];
        const to = byId[edge.to];
        return from && to ? <line key={edge.id || index} x1={from.px} y1={from.py} x2={to.px} y2={to.py}
          stroke="#a9bfd1" strokeWidth="0.5"><title>{edge.label || `${from.label || from.id} → ${to.label || to.id}`}</title></line> : null;
      })}
      {positioned.map((node) => <g key={node.id} onClick={() => setSelected(node.id)}
        role="button" tabIndex="0" onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(node.id); }
        }} aria-label={node.label || node.id}>
        <circle cx={node.px} cy={node.py} r={selected === node.id ? 4.2 : 3.4}
          fill={selected === node.id ? "#397eb8" : "#719bbb"} />
        <text x={node.px} y={node.py + 7} textAnchor="middle">{node.label || node.id}</text>
      </g>)}
    </svg>
    {active && <p className="network-detail"><strong>{active.label || active.id}</strong>{active.description && ` · ${active.description}`}</p>}
  </>;
}

function VirtualTable({ view }) {
  const rows = view.rows || [];
  const columns = view.columns || [];
  const [scrollTop, setScrollTop] = useState(0);
  const height = 260;
  const rowHeight = 40;
  if (!columns.length) return <EmptyView>请为表格指定 columns。</EmptyView>;
  const { start, end } = visibleRows(rows.length, scrollTop, height, rowHeight);
  const grid = { gridTemplateColumns: `repeat(${columns.length}, minmax(130px, 1fr))` };
  return <div className="virtual-table" role="table" aria-rowcount={rows.length + 1}
    aria-label={view.ariaLabel || view.title || "数据表"}>
    <div className="virtual-inner" style={{ minWidth: columns.length * 130 }}>
    <div className="virtual-header" role="row" aria-rowindex={1} style={grid}>
      {columns.map((column) => <span role="columnheader" key={column.key}>{column.label}</span>)}
    </div>
    <div className="virtual-scroll" style={{ height }} onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}>
      <div style={{ height: rows.length * rowHeight, position: "relative" }}>
        {rows.slice(start, end).map((row, offset) => <div className="virtual-row" role="row"
          aria-rowindex={start + offset + 2} key={row.id ?? start + offset}
          style={{ ...grid, top: (start + offset) * rowHeight, height: rowHeight }}>
          {columns.map((column) => <span role="cell" key={column.key} title={String(row[column.key] ?? "")}>{String(row[column.key] ?? "—")}</span>)}
        </div>)}
      </div>
    </div>
    </div>
    <p className="view-caption">共 {rows.length.toLocaleString()} 条记录</p>
  </div>;
}

function CustomView({ view, context }) {
  const target = useRef(null);
  const renderer = window.SOCIETY0_RENDERERS?.[view.renderer];
  useEffect(() => {
    if (typeof renderer !== "function" || !target.current) return;
    const cleanup = renderer(target.current, view, context);
    return () => { if (typeof cleanup === "function") cleanup(); target.current?.replaceChildren(); };
  }, [renderer, view, context]);
  if (typeof renderer !== "function") return <EmptyView>缺少自定义呈现函数：{view.renderer || "未指定"}</EmptyView>;
  return <div ref={target} className="custom-view" />;
}

export function ViewTabs({ views, context }) {
  const [viewId, setViewId] = useState(views[0]?.id || "");
  const view = views.find((item) => item.id === viewId) || views[0];
  if (!view) return <EmptyView>这个模块尚未添加呈现方式。</EmptyView>;
  return <div className="module-views">
    {views.length > 1 && <div className="small-tabs" role="tablist" aria-label="呈现方式">
      {views.map((item) => <button role="tab" aria-selected={view.id === item.id} key={item.id}
        className={view.id === item.id ? "active" : ""} onClick={() => setViewId(item.id)}>{item.title || item.id}</button>)}
    </div>}
    <div className="view-body" key={view.id}>
      {view.type === "candlestick" && <Candlestick view={view} />}
      {view.type === "network" && <Network view={view} />}
      {view.type === "table" && <VirtualTable view={view} />}
      {view.type === "timeseries" && <TimeSeries view={view} />}
      {view.type === "text" && <div className="text-view">{(view.paragraphs || [view.text]).filter(Boolean).map((text, index) => <p key={index}>{text}</p>)}</div>}
      {view.type === "metric" && <div className="metric-view">{(view.items || []).map((item, index) => <div key={item.label || index}>
        <span>{item.label}</span><strong>{item.value ?? "—"}{item.unit && <small> {item.unit}</small>}</strong>{item.note && <p>{item.note}</p>}
      </div>)}</div>}
      {view.type === "custom" && <CustomView view={view} context={context} />}
      {!["candlestick", "network", "table", "timeseries", "text", "metric", "custom"].includes(view.type) && <EmptyView>未知的呈现类型：{view.type}</EmptyView>}
    </div>
  </div>;
}
