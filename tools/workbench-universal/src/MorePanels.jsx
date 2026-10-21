import React, { useState } from 'react';
import { formatTime } from './data.js';
import { StateBlock } from './ui.jsx';

/* ---------- 工件目录（WB-01-05） ---------- */

export function ArtifactsPanel({ dataset }) {
  const files = dataset?.files || [];
  if (!files.length) return <StateBlock kind="empty" title="还没有工件" tight>生成资料后，目录与大小会出现在这里。</StateBlock>;
  const total = files.reduce((sum, file) => sum + (file.size || 0), 0);
  return (
    <div className="artifacts">
      <p className="artifacts-summary">{files.length} 个文件 · 共 {formatSize(total)}{dataset.truncated ? ' · 目录未完整包含' : ''}</p>
      <ul>
        {files.map((file) => (
          <li key={file.path}>
            <span className="artifact-type">{file.type || '文件'}</span>
            <span className="artifact-path mono">{file.path}</span>
            <span className="artifact-size">{file.size != null ? formatSize(file.size) : '—'}</span>
          </li>
        ))}
      </ul>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

function formatSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

/* ---------- K 线（WB-04-06） ---------- */

export function CandlesPanel({ dataset }) {
  const bars = dataset?.bars || [];
  if (!bars.length) return <StateBlock kind="empty" title="没有价格区间" tight>开高低收随资料进入。</StateBlock>;
  const width = 620;
  const height = 200;
  const pad = { left: 8, right: 46, top: 12, bottom: 20 };
  const highs = bars.map((bar) => bar.high);
  const lows = bars.map((bar) => bar.low);
  const max = Math.max(...highs);
  const min = Math.min(...lows);
  const span = max - min || 1;
  const slot = (width - pad.left - pad.right) / bars.length;
  const y = (value) => pad.top + (height - pad.top - pad.bottom) * (1 - (value - min) / span);

  return (
    <div className="candles">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={dataset.title || 'K 线'}>
        <line x1={pad.left} x2={width - pad.right} y1={height - pad.bottom} y2={height - pad.bottom} stroke="#eef3f7" />
        {bars.map((bar, index) => {
          const cx = pad.left + slot * index + slot / 2;
          const rise = bar.close >= bar.open;
          const color = rise ? '#5f9c86' : '#c0867f';
          const bodyTop = y(Math.max(bar.open, bar.close));
          const bodyBottom = y(Math.min(bar.open, bar.close));
          const incomplete = bar.open == null || bar.high == null || bar.low == null || bar.close == null;
          if (incomplete) {
            return <rect key={bar.t} x={cx - 3} y={pad.top} width="6" height={height - pad.top - pad.bottom} fill="#f0f4f7" rx="2" />;
          }
          return (
            <g key={bar.t}>
              <line x1={cx} x2={cx} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth="1" />
              <rect x={cx - Math.max(2, slot * 0.28)} y={bodyTop} width={Math.max(4, slot * 0.56)} height={Math.max(1.5, bodyBottom - bodyTop)} fill={color} rx="1" />
            </g>
          );
        })}
        <text x={width - pad.right + 6} y={y(max) + 4} fontSize="9" fill="#7b93a6">{formatNumber(max)}</text>
        <text x={width - pad.right + 6} y={y(min) + 4} fontSize="9" fill="#7b93a6">{formatNumber(min)}</text>
      </svg>
      <p className="panel-source">每根为开高低收；缺任一价格显示为灰色占位，不补值。{dataset.source ? `来源：${dataset.source}` : ''}</p>
    </div>
  );
}

function formatNumber(value) {
  if (value == null) return '—';
  return Number.isInteger(value) ? value.toLocaleString('zh-CN') : value.toFixed(2);
}

/* ---------- 时间映射检查表（WB-03-06） ---------- */

export function TimeMapPanel({ dataset }) {
  const rows = dataset?.rows || [];
  if (!rows.length) return <StateBlock kind="empty" title="没有时间映射" tight>步骤、业务日期与执行时间随资料进入。</StateBlock>;
  return (
    <table className="timemap">
      <thead><tr><th>步骤</th><th>业务区间</th><th>执行时间</th><th>备注</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.step}>
            <td>{row.label || `第 ${row.step} 步`}</td>
            <td>{row.start ? `${formatTime(row.start)} ~ ${formatTime(row.end)}` : '—'}</td>
            <td>{row.executedAt ? formatTime(row.executedAt) : '未记录'}</td>
            <td>{row.note || ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ---------- 检查点索引（WB-02-06） ---------- */

export function CheckpointsPanel({ dataset }) {
  const checkpoints = dataset?.checkpoints || [];
  if (!checkpoints.length) return <StateBlock kind="empty" title="还没有完整点" tight>每个完整步骤发布后会列出。</StateBlock>;
  return (
    <ul className="checkpoints">
      {checkpoints.map((item) => (
        <li key={item.id}>
          <span className={`cp-dot ${item.availability || 'ready'}`} aria-hidden="true" />
          <strong>{item.label || item.id}</strong>
          <small>{item.businessTime ? formatTime(item.businessTime) : '业务时间未提供'}</small>
          <em>{item.availability === 'partial' ? '部分可读' : item.availability === 'error' ? '读取失败' : '可读'}</em>
        </li>
      ))}
    </ul>
  );
}
