import React, { useEffect, useMemo, useRef, useState } from 'react';
import { RUN_STATUS_LABELS, formatTime } from './data.js';
import { StateBlock } from './ui.jsx';
import { WorldDiagram } from './WorldDiagram.jsx';

function statusLabel(status) {
  return RUN_STATUS_LABELS[status] || RUN_STATUS_LABELS.unknown;
}

function worldLabel(worlds, worldId) {
  return worlds.find((world) => world.id === worldId)?.label || '世界待提供';
}

const BOARD_COLUMNS = [
  { id: 'queued', label: '等待', match: ['queued'] },
  { id: 'running', label: '进行中', match: ['running'] },
  { id: 'completed', label: '已完成', match: ['completed'] },
  { id: 'failed', label: '失败', match: ['failed', 'cancelled'] },
  { id: 'other', label: '其他', match: ['unknown', 'paused'] },
];

function columnOf(status) {
  const found = BOARD_COLUMNS.find((column) => column.match.includes(status || 'unknown'));
  return found ? found.id : 'other';
}

/** 运行入口：打开模态框查看看板与平行世界；只做检视，不改观察上下文。 */
export function RunLauncher({ runs, worlds, observations, onOpenSetup }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('board');
  const [detailRunId, setDetailRunId] = useState('');
  const [query, setQuery] = useState('');
  const trigger = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape') {
        if (detailRunId) setDetailRunId('');
        else { setOpen(false); trigger.current?.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, detailRunId]);

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    if (!text) return runs;
    return runs.filter((run) => `${run.label} ${worldLabel(worlds, run.worldId)} ${run.id}`.toLowerCase().includes(text));
  }, [runs, worlds, query]);

  const detailRun = runs.find((run) => run.id === detailRunId) || null;
  const detailObservations = detailRun ? observations.filter((obs) => obs.runId === detailRun.id) : [];

  return (
    <>
      <button
        type="button"
        ref={trigger}
        className="run-launcher"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => { setOpen(true); setDetailRunId(''); }}
      >
        运行 · {runs.length}
        <span className="caret" aria-hidden="true">▾</span>
      </button>

      {open ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
          <div className="modal run-modal" role="dialog" aria-modal="true" aria-label="运行与平行世界">
            <header className="modal-head">
              {detailRun ? (
                <>
                  <button type="button" className="link-button" onClick={() => setDetailRunId('')}>← 返回看板</button>
                  <strong>{detailRun.label}</strong>
                </>
              ) : (
                <div className="segmented" role="tablist" aria-label="运行视图">
                  <button type="button" role="tab" aria-selected={view === 'board'} onClick={() => setView('board')}>运行看板</button>
                  <button type="button" role="tab" aria-selected={view === 'worlds'} onClick={() => setView('worlds')}>平行世界</button>
                </div>
              )}
              <span className="spacer" />
              <button type="button" className="link-button" onClick={() => { setOpen(false); onOpenSetup?.(); }}>配置视图</button>
              <button type="button" className="icon-button" aria-label="关闭" onClick={() => setOpen(false)}>×</button>
            </header>

            <div className="modal-body">
              {detailRun ? (
                <RunDetailBody run={detailRun} world={worlds.find((world) => world.id === detailRun.worldId)} observations={detailObservations} />
              ) : view === 'board' ? (
                <>
                  <div className="run-filters">
                    <input type="search" placeholder="搜索运行、世界或编号" aria-label="搜索运行" value={query} onChange={(event) => setQuery(event.target.value)} />
                    <small>{visible.length} / {runs.length} 次运行</small>
                  </div>
                  {runs.length ? (
                    <div className="kanban">
                      {BOARD_COLUMNS.map((column) => {
                        const items = visible.filter((run) => columnOf(run.status) === column.id);
                        return (
                          <section className="kanban-column" key={column.id} aria-label={column.label}>
                            <header><strong>{column.label}</strong><span>{items.length}</span></header>
                            <ul>
                              {items.map((run) => (
                                <li key={run.id}>
                                  <button type="button" className="kanban-card" onClick={() => setDetailRunId(run.id)}>
                                    <strong>{run.label}</strong>
                                    <small>{worldLabel(worlds, run.worldId)}</small>
                                    <span className={`run-status ${run.status || 'unknown'}`}>{statusLabel(run.status)}</span>
                                    <small>{run.dataThrough ? `资料截至 ${formatTime(run.dataThrough)}` : '尚无完整资料'}</small>
                                  </button>
                                </li>
                              ))}
                              {!items.length ? <li className="kanban-empty">暂无</li> : null}
                            </ul>
                          </section>
                        );
                      })}
                    </div>
                  ) : (
                    <StateBlock kind="unconfigured" title="还没有运行资料">世界计划可以先在配置里核对；运行出现后会进入这里。</StateBlock>
                  )}
                </>
              ) : (
                <WorldDiagram worlds={worlds} runs={runs} selectedRunId={detailRunId} onSelectRun={(runId) => setDetailRunId(runId)} />
              )}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function RunDetailBody({ run, world, observations }) {
  return (
    <>
      <dl className="kv">
        <dt>运行编号</dt><dd className="mono">{run.id}</dd>
        <dt>所属世界</dt><dd>{world?.label || '世界待提供'}</dd>
        {world?.parentWorldId ? (<><dt>来源世界</dt><dd>{world.parentWorldId}</dd></>) : null}
        {world?.plannedSource ? (<><dt>来源完整点</dt><dd>{world.plannedSource}</dd></>) : null}
        <dt>资料截至</dt><dd>{run.dataThrough ? formatTime(run.dataThrough) : '尚无完整资料'}</dd>
        <dt>运行状态</dt><dd>{statusLabel(run.status)}</dd>
      </dl>
      <div className="modal-section">
        <div className="section-title"><h2>可用时点</h2><span>{observations.length} 个</span></div>
        {observations.length ? (
          <ul className="obs-list">
            {observations.map((obs) => (
              <li key={obs.id} className="obs-item">
                <span className="obs-label">{obs.label}</span>
                <span className="obs-time">{formatTime(obs.businessTime)}</span>
                <span className="badge">{obs.availability === 'partial' ? '部分资料' : obs.availability === 'ready' ? '可读' : '待补'}</span>
              </li>
            ))}
          </ul>
        ) : (
          <StateBlock kind="awaiting" title="这次运行还没有时点资料" tight>时点与结果随资料更新进入。</StateBlock>
        )}
      </div>
    </>
  );
}
