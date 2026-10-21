import React, { useState } from 'react';
import { formatTime } from './data.js';
import { Badge, StateBlock } from './ui.jsx';

/* ---------- 收益矩阵 ---------- */

export function MatrixPanel({ dataset }) {
  const rows = dataset?.rows || [];
  const columns = dataset?.columns || [];
  const cells = dataset?.cells || [];
  if (!rows.length || !columns.length) return <StateBlock kind="empty" title="没有博弈资料" tight>需要参与方与收益格。</StateBlock>;
  return (
    <div className="matrix">
      <table className="matrix-table">
        <thead>
          <tr><th>{dataset.rowLabel || '行方 \\ 列方'}</th>{columns.map((col) => <th key={col.id}>{col.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <th scope="row">{row.label}</th>
              {columns.map((col) => {
                const cell = cells.find((item) => item.row === row.id && item.column === col.id);
                if (!cell) return <td key={col.id} className="matrix-missing">—</td>;
                return (
                  <td key={col.id}>
                    <strong>{cell.rowPayoff}</strong>
                    <span>{cell.colPayoff}</span>
                    {cell.note ? <small>{cell.note}</small> : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="panel-source">每格上行是行方收益，下行是列方收益；未定义格保留空，不补零。{dataset.source ? ` 来源：${dataset.source}` : ''}</p>
    </div>
  );
}

/* ---------- 策略份额 ---------- */

export function StrategyPanel({ dataset }) {
  const rounds = dataset?.rounds || [];
  if (!rounds.length) return <StateBlock kind="empty" title="没有策略资料" tight>回合与份额随资料进入。</StateBlock>;
  const strategies = dataset?.strategies || [];
  const colors = ['#4a8fc2', '#bf8755', '#7fb09a', '#8c7fb0', '#c98b84'];
  return (
    <div className="strategy">
      <table className="strategy-table">
        <thead><tr><th>回合</th>{strategies.map((strategy, index) => <th key={strategy.id}><i style={{ background: colors[index % colors.length] }} />{strategy.label}</th>)}</tr></thead>
        <tbody>
          {rounds.map((round) => (
            <tr key={round.id || round.t}>
              <td>{round.label || formatTime(round.t)}</td>
              {strategies.map((strategy) => <td key={strategy.id}>{round.shares?.[strategy.id] == null ? '—' : `${Math.round(round.shares[strategy.id] * 100)}%`}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 预测区间与误差 ---------- */

export function ForecastPanel({ dataset }) {
  const series = dataset?.series || [];
  if (!series.length) return <StateBlock kind="empty" title="没有预测资料" tight>预测、区间与观测随资料进入。</StateBlock>;
  const width = 620;
  const height = 210;
  const pad = { left: 36, right: 12, top: 14, bottom: 22 };
  const all = series.flatMap((line) => line.points || []);
  const xs = all.map((point) => point.t);
  const ys = all.flatMap((point) => [point.v, point.lo, point.hi].filter((value) => typeof value === 'number'));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const rawMin = Math.min(...ys);
  const rawMax = Math.max(...ys);
  const padding = (rawMax - rawMin || 1) * 0.15;
  const minY = rawMin - padding;
  const maxY = rawMax + padding;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (value) => pad.left + (maxX === minX ? plotW / 2 : ((value - minX) / (maxX - minX)) * plotW);
  const y = (value) => pad.top + plotH - (maxY === minY ? plotH / 2 : ((value - minY) / (maxY - minY)) * plotH);
  const colors = ['#4a8fc2', '#7fb09a', '#bf8755', '#8c7fb0'];

  return (
    <div className="forecast">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="预测与观测" style={{ height }}>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => <line key={ratio} x1={pad.left} x2={width - pad.right} y1={pad.top + plotH * ratio} y2={pad.top + plotH * ratio} stroke="#eef3f7" />)}
        {series.map((line, index) => {
          const color = colors[index % colors.length];
          const upper = (line.points || []).filter((point) => typeof point.hi === 'number').map((point) => `${x(point.t)},${y(point.hi)}`);
          const lower = (line.points || []).filter((point) => typeof point.lo === 'number').map((point) => `${x(point.t)},${y(point.lo)}`).reverse();
          const band = upper.length && lower.length ? `M ${upper.join(' L ')} L ${lower.join(' L ')} Z` : null;
          return (
            <g key={line.key || index}>
              {band ? <path d={band} fill={color} opacity="0.12" /> : null}
              <polyline points={(line.points || []).map((point) => `${x(point.t)},${y(point.v)}`).join(' ')} fill="none" stroke={color} strokeWidth="1.8" strokeDasharray={line.projected ? '4 3' : undefined} vectorEffect="non-scaling-stroke" />
            </g>
          );
        })}
      </svg>
      <ul className="forecast-legend">
        {series.map((line, index) => (
          <li key={line.key || index}><i style={{ background: colors[index % colors.length] }} />{line.label || line.key}{line.projected ? '（预测）' : ''}{line.unit ? ` · ${line.unit}` : ''}</li>
        ))}
      </ul>
      {dataset.errors?.length ? (
        <table className="forecast-errors">
          <thead><tr><th>范围</th><th>误差</th><th>基线</th></tr></thead>
          <tbody>
            {dataset.errors.map((row) => <tr key={row.id || row.label}><td>{row.label}</td><td>{row.error}</td><td>{row.baseline ?? '—'}</td></tr>)}
          </tbody>
        </table>
      ) : null}
      {dataset.source ? <p className="panel-source">来源：{dataset.source}{dataset.publishedAt ? ` · 发布于 ${formatTime(dataset.publishedAt)}` : ''}</p> : null}
    </div>
  );
}

/* ---------- 诊断与用量 ---------- */

export function DiagnosticsPanel({ dataset }) {
  const runs = dataset?.runs || [];
  const calls = dataset?.calls || [];
  return (
    <div className="diagnostics">
      {runs.length ? (
        <div className="diag-grid">
          {runs.map((run) => (
            <div className="diag" key={run.id}>
              <span>{run.label}</span>
              <strong>{run.activations ?? '—'}<small> 次激活</small></strong>
              <small>{run.toolCalls != null ? `${run.toolCalls} 次工具调用` : '工具调用未记录'}{run.incomplete ? ` · ${run.incomplete} 未完成` : ''}</small>
            </div>
          ))}
        </div>
      ) : null}
      {calls.length ? (
        <table className="diag-table">
          <thead><tr><th>调用</th><th>耗时</th><th>结果</th></tr></thead>
          <tbody>
            {calls.map((call) => (
              <tr key={call.id}>
                <td className="mono">{call.name}</td>
                <td>{call.ms != null ? `${call.ms} ms` : '—'}</td>
                <td><Badge state={call.status === 'failed' ? 'error' : call.status === 'completed' ? 'ready' : undefined}>{call.status || '待提供'}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {!runs.length && !calls.length ? <StateBlock kind="empty" title="没有诊断资料" tight>导入的用量与调用记录会出现在这里。</StateBlock> : null}
      {dataset.source ? <p className="panel-source">来源：{dataset.source}。这是导入的展示资料，页面不发起端点调用。</p> : null}
    </div>
  );
}
