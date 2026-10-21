import React, { useMemo, useState } from 'react';
import { formatTime } from './data.js';
import { Badge, StateBlock } from './ui.jsx';

/* ---------- 多世界比较 ---------- */

const SERIES_COLORS = ['#4a8fc2', '#bf8755', '#7fb09a', '#8c7fb0', '#c98b84', '#6fa6a0', '#a98bb5'];

export function ComparePanel({ dataset }) {
  const worlds = dataset?.worlds || [];
  const [metric, setMetric] = useState(dataset?.defaultMetric || worlds[0]?.series?.[0]?.key || '');
  const [mode, setMode] = useState('absolute');

  if (!worlds.length) {
    return <StateBlock kind="empty" title="还没有可比较的世界" tight>至少需要两个世界的结果资料。</StateBlock>;
  }

  const allSeries = worlds.flatMap((world) => (world.series || []).map((line) => ({
    ...line,
    worldId: world.id,
    worldLabel: world.label,
    through: world.through,
  })));
  const keys = [...new Set(allSeries.map((line) => line.key))];
  const active = allSeries.filter((line) => line.key === metric);
  const unit = active[0]?.unit || '';

  const points = active.flatMap((line) => line.points || []);
  const empty = !points.length;

  const width = 660;
  const height = 220;
  const pad = { left: 44, right: 56, top: 16, bottom: 26 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const tValues = points.map((point) => point.t);
  const minX = tValues.length ? Math.min(...tValues) : 0;
  const maxX = tValues.length ? Math.max(...tValues) : 1;

  // 基准世界用于差值；缺失基准时禁用相对模式。
  const baseline = worlds.find((world) => world.isBaseline) || worlds[0];
  const baselineLine = baseline.series?.find((line) => line.key === metric);
  const baselineAt = new Map((baselineLine?.points || []).map((point) => [point.t, point.v]));

  const values = active.flatMap((line) => (line.points || []).map((point) => {
    const base = baselineAt.get(point.t);
    if (mode === 'relative') {
      if (!base) return null;
      return ((point.v - base) / Math.abs(base)) * 100;
    }
    return point.v;
  })).filter((value) => value !== null);
  const minY = values.length ? Math.min(...values, 0) : 0;
  const maxY = values.length ? Math.max(...values) : 1;

  const x = (value) => pad.left + (maxX === minX ? plotW / 2 : ((value - minX) / (maxX - minX)) * plotW);
  const y = (value) => pad.top + plotH - (maxY === minY ? plotH / 2 : ((value - minY) / (maxY - minY)) * plotH);

  const gaps = worlds.filter((world) => !world.series?.some((line) => line.key === metric && line.points?.length));

  return (
    <div className="compare">
      <div className="compare-tools">
        <select aria-label="比较指标" value={metric} onChange={(event) => setMetric(event.target.value)}>
          {keys.map((key) => <option key={key} value={key}>{allSeries.find((line) => line.key === key)?.label || key}</option>)}
        </select>
        <div className="segmented" role="group" aria-label="比较方式">
          <button type="button" aria-pressed={mode === 'absolute'} onClick={() => setMode('absolute')}>绝对值</button>
          <button type="button" aria-pressed={mode === 'relative'} onClick={() => setMode('relative')} disabled={!baselineLine}>相对基准</button>
        </div>
        <small>{unit || '单位待提供'}</small>
      </div>

      {empty ? (
        <StateBlock kind="partial" title="这个指标没有数据点" tight>换一个指标，或等待资料补充。</StateBlock>
      ) : (
        <svg viewBox={`0 0 ${width} ${height}`} className="compare-chart" role="img" aria-label="多世界比较">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
            <line key={ratio} x1={pad.left} x2={width - pad.right} y1={pad.top + plotH * ratio} y2={pad.top + plotH * ratio} stroke="#eef3f7" />
          ))}
          <line x1={pad.left} x2={pad.left} y1={pad.top} y2={pad.top + plotH} stroke="#dfe7ee" />
          {active.map((line, index) => {
            const pointsForLine = (line.points || []).map((point) => {
              const base = baselineAt.get(point.t);
              const value = mode === 'relative' ? (base ? ((point.v - base) / Math.abs(base)) * 100 : null) : point.v;
              return value === null ? null : `${x(point.t).toFixed(1)},${y(value).toFixed(1)}`;
            }).filter(Boolean);
            if (!pointsForLine.length) return null;
            return (
              <polyline
                key={line.worldId || index}
                points={pointsForLine.join(' ')}
                fill="none"
                stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
                strokeWidth="1.9"
                vectorEffect="non-scaling-stroke"
                strokeDasharray={baseline.id === line.worldId ? undefined : '4 3'}
              />
            );
          })}
          <text x={pad.left - 6} y={pad.top - 4} fontSize="9" fill="#8292a1" textAnchor="end">{mode === 'relative' ? '%' : unit}</text>
          {active.map((line, index) => {
            const last = line.points?.at(-1);
            if (!last) return null;
            const base = baselineAt.get(last.t);
            const value = mode === 'relative' ? (base ? ((last.v - base) / Math.abs(base)) * 100 : null) : last.v;
            if (value === null) return null;
            return <text key={`label-${line.worldId || index}`} x={width - pad.right + 6} y={y(value) + 3} fontSize="9" fill={SERIES_COLORS[index % SERIES_COLORS.length]}>{formatNumber(value)}</text>;
          })}
        </svg>
      )}

      <ul className="compare-legend">
        {active.map((line, index) => (
          <li key={line.worldId || index}>
            <i className="swatch" style={{ background: SERIES_COLORS[index % SERIES_COLORS.length], opacity: baseline.id === line.worldId ? 1 : 0.7 }} />
            {line.worldLabel || line.worldId}
            {baseline.id === line.worldId ? <span className="badge">基准</span> : null}
            {line.through ? <small>至 {formatTime(line.through)}</small> : null}
          </li>
        ))}
      </ul>

      {gaps.length ? (
        <p className="compare-gap">缺少该指标的世界：{gaps.map((world) => world.label || world.id).join('、')}。比较只在共同范围内成立，未覆盖日期保留缺口。</p>
      ) : null}
      {mode === 'relative' && baselineLine ? <p className="panel-source">基准：{baseline.label || baseline.id}；相对值 =（世界值 − 基准值）/ |基准值|。基准为零时不计相对差。</p> : null}
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 柱状 / 分组比较 ---------- */

export function BarsPanel({ dataset }) {
  const bars = dataset?.bars || [];
  if (!bars.length) return <StateBlock kind="empty" title="没有条形数据" tight>资料提供后显示。</StateBlock>;
  const max = Math.max(...bars.map((bar) => Math.abs(bar.value)), 1);
  return (
    <div className="bars">
      {bars.map((bar) => (
        <div className="bar-row" key={bar.id || bar.label}>
          <span className="bar-label">{bar.label}</span>
          <div className="bar-track">
            <div className={`bar-fill${bar.value < 0 ? ' negative' : ''}`} style={{ width: `${(Math.abs(bar.value) / max) * 100}%` }} />
          </div>
          <span className="bar-value">{formatNumber(bar.value)}{bar.unit ? ` ${bar.unit}` : ''}</span>
        </div>
      ))}
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 分布 ---------- */

export function DistributionPanel({ dataset }) {
  const groups = dataset?.groups || [];
  if (!groups.length) return <StateBlock kind="empty" title="没有分布数据" tight>资料提供后显示。</StateBlock>;
  const all = groups.flatMap((group) => group.values || []);
  const min = Math.min(...all);
  const max = Math.max(...all);
  return (
    <div className="distribution">
      {groups.map((group, index) => {
        const values = [...(group.values || [])].sort((a, b) => a - b);
        const q1 = values[Math.floor(values.length * 0.25)] ?? min;
        const median = values[Math.floor(values.length * 0.5)] ?? min;
        const q3 = values[Math.floor(values.length * 0.75)] ?? min;
        const span = max - min || 1;
        return (
          <div className="box-row" key={group.id || group.label}>
            <span className="box-label">{group.label}</span>
            <div className="box-track">
              <div className="box-whisker" style={{ left: `${((values[0] - min) / span) * 100}%`, width: `${((values.at(-1) - values[0]) / span) * 100}%` }} />
              <div
                className="box-body"
                style={{
                  left: `${((q1 - min) / span) * 100}%`,
                  width: `${Math.max(2, ((q3 - q1) / span) * 100)}%`,
                  background: SERIES_COLORS[index % SERIES_COLORS.length],
                }}
              />
              <i className="box-median" style={{ left: `${((median - min) / span) * 100}%` }} />
            </div>
            <span className="box-value">{formatNumber(median)}{group.unit ? ` ${group.unit}` : ''}</span>
          </div>
        );
      })}
      <p className="panel-source">中位数与四分位区间；共 {all.length} 个样本。{dataset.source ? `来源：${dataset.source}` : ''}</p>
    </div>
  );
}

/* ---------- 热力 ---------- */

export function HeatPanel({ dataset }) {
  const rows = dataset?.rows || [];
  const columns = dataset?.columns || [];
  if (!rows.length || !columns.length) return <StateBlock kind="empty" title="没有热力数据" tight>需要行、列与数值。</StateBlock>;
  const values = rows.flatMap((row) => columns.map((col) => row.values?.[col.key])).filter((value) => typeof value === 'number');
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return (
    <div className="heat">
      <div className="heat-grid" style={{ gridTemplateColumns: `minmax(70px, 0.8fr) repeat(${columns.length}, minmax(38px, 1fr))` }}>
        <span />
        {columns.map((col) => <span key={col.key} className="heat-col">{col.label}</span>)}
        {rows.map((row) => (
          <React.Fragment key={row.id || row.label}>
            <span className="heat-row">{row.label}</span>
            {columns.map((col) => {
              const value = row.values?.[col.key];
              const ratio = typeof value === 'number' ? (value - min) / span : null;
              return (
                <span
                  key={col.key}
                  className="heat-cell"
                  title={typeof value === 'number' ? `${row.label} · ${col.label}：${formatNumber(value)}` : '未提供'}
                  style={{ background: ratio === null ? '#f4f7f9' : `rgba(74,143,194,${0.08 + ratio * 0.55})`, color: ratio !== null && ratio > 0.62 ? '#fff' : '#365b8f' }}
                >
                  {typeof value === 'number' ? value.toLocaleString('zh-CN') : '—'}
                </span>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      <p className="panel-source">颜色按当前范围最小值到最大值映射。{dataset.source ? `来源：${dataset.source}` : ''}</p>
    </div>
  );
}

/* ---------- 市场盘口 ---------- */

export function OrderBookPanel({ dataset }) {
  const bids = dataset?.bids || [];
  const asks = dataset?.asks || [];
  if (!bids.length && !asks.length) return <StateBlock kind="empty" title="没有盘口资料" tight>报价侧尚未提供。</StateBlock>;
  const maxQty = Math.max(...[...bids, ...asks].map((row) => row.quantity || 0), 1);
  return (
    <div className="orderbook">
      <table>
        <thead><tr><th>方向</th><th>价格</th><th>数量</th><th>占比</th></tr></thead>
        <tbody>
          {asks.map((row, index) => (
            <tr key={`a${index}`} className="ask">
              <td><Badge>卖</Badge></td>
              <td>{formatNumber(row.price)}</td>
              <td>{formatNumber(row.quantity)}</td>
              <td><span className="depth" style={{ width: `${((row.quantity || 0) / maxQty) * 100}%` }} /></td>
            </tr>
          ))}
          {bids.map((row, index) => (
            <tr key={`b${index}`} className="bid">
              <td><Badge state="ready">买</Badge></td>
              <td>{formatNumber(row.price)}</td>
              <td>{formatNumber(row.quantity)}</td>
              <td><span className="depth" style={{ width: `${((row.quantity || 0) / maxQty) * 100}%` }} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {dataset.last ? <p className="panel-source">最新成交：{formatNumber(dataset.last.price)} × {formatNumber(dataset.last.quantity)}。{dataset.source ? `来源：${dataset.source}` : ''}</p> : null}
    </div>
  );
}

function formatNumber(value) {
  if (value == null || Number.isNaN(value)) return '—';
  const abs = Math.abs(value);
  if (abs >= 100000000) return `${(value / 100000000).toFixed(2)} 亿`;
  if (abs >= 10000) return `${(value / 10000).toFixed(2)} 万`;
  if (Number.isInteger(value)) return value.toLocaleString('zh-CN');
  return value.toFixed(2).replace(/\.00$/, '');
}
