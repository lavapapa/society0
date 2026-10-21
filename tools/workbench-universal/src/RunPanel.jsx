import React, { useEffect, useMemo, useRef, useState } from 'react';
import { RUN_STATUS_LABELS, formatTime } from './data.js';
import { StateBlock } from './ui.jsx';
import { useAnchoredPopover } from './useAnchoredPopover.js';

function statusLabel(status) {
  return RUN_STATUS_LABELS[status] || RUN_STATUS_LABELS.unknown;
}

function worldLabel(worlds, worldId) {
  return worlds.find((world) => world.id === worldId)?.label || '世界待提供';
}

/** 顶栏运行选择：看板与世界视图都以 run 为选择节点。 */
export function RunPicker({ runs, worlds, selection, select, version }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('board');
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const trigger = useRef(null);
  const { anchorRef: popoverAnchor, popoverRef, style: popoverStyle } = useAnchoredPopover(open, 560);

  const current = runs.find((run) => run.id === selection.runId) || null;

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    return runs.filter((run) => {
      if (statusFilter && run.status !== statusFilter) return false;
      if (!text) return true;
      return `${run.label} ${worldLabel(worlds, run.worldId)} ${run.id}`.toLowerCase().includes(text);
    });
  }, [runs, worlds, query, statusFilter]);

  const grouped = useMemo(() => {
    const map = new Map();
    for (const world of worlds) map.set(world.id, []);
    for (const run of visible) {
      if (!map.has(run.worldId)) map.set(run.worldId, []);
      map.get(run.worldId).push(run);
    }
    return [...map.entries()].filter(([, items]) => items.length > 0);
  }, [worlds, visible]);

  const choose = (runId) => {
    const run = runs.find((item) => item.id === runId);
    select({ runId, worldId: run?.worldId || '', pageId: 'runs' });
    setOpen(false);
    trigger.current?.focus();
  };

  const statuses = useMemo(() => [...new Set(runs.map((run) => run.status || 'unknown'))], [runs]);

  return (
    <div className="run-picker">
      <button
        type="button"
        ref={(node) => { trigger.current = node; popoverAnchor.current = node; }}
        className="run-trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="run-trigger-label">
          {current ? current.label : version ? '配置视图 · 尚无运行' : '尚无配置'}
        </span>
        {current ? <span className={`run-status ${current.status || 'unknown'}`}>{statusLabel(current.status)}</span> : null}
        <span className="caret" aria-hidden="true">▾</span>
      </button>

      {open ? (
        <div className="run-popover" role="dialog" aria-label="运行选择" ref={popoverRef} style={popoverStyle}>
          <div className="run-popover-head">
            <div className="segmented" role="tablist" aria-label="运行视图">
              <button type="button" role="tab" aria-selected={view === 'board'} onClick={() => setView('board')}>运行看板</button>
              <button type="button" role="tab" aria-selected={view === 'worlds'} onClick={() => setView('worlds')}>平行世界</button>
            </div>
            <button type="button" className="link-button" onClick={() => { select({ pageId: 'setup', runId: '' }); setOpen(false); }}>
              配置视图
            </button>
          </div>

          {runs.length ? (
            <>
              <div className="run-filters">
                <input type="search" placeholder="搜索运行、世界或编号" aria-label="搜索运行" value={query} onChange={(event) => setQuery(event.target.value)} />
                <select aria-label="按状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="">全部状态</option>
                  {statuses.map((status) => <option key={status} value={status}>{statusLabel(status)}</option>)}
                </select>
              </div>

              <div className="run-popover-body">
                {view === 'board' ? (
                  visible.length ? (
                    <ul className="run-rows">
                      {visible.map((run) => (
                        <li key={run.id}>
                          <button type="button" className={`run-row${selection.runId === run.id ? ' selected' : ''}`} onClick={() => choose(run.id)}>
                            <span className="run-row-main">
                              <strong>{run.label}</strong>
                              <small>{worldLabel(worlds, run.worldId)} · {run.versionId || '版本待提供'}</small>
                            </span>
                            <span className={`run-status ${run.status || 'unknown'}`}>{statusLabel(run.status)}</span>
                            <span className="run-row-through">{run.dataThrough ? formatTime(run.dataThrough) : '尚无完整资料'}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <StateBlock kind="empty" title="没有符合条件的运行" tight>调整搜索或状态筛选后重试。</StateBlock>
                  )
                ) : grouped.length ? (
                  <div className="world-groups">
                    {grouped.map(([worldId, items]) => {
                      const world = worlds.find((item) => item.id === worldId);
                      return (
                        <section key={worldId} className="world-group">
                          <header>
                            <strong>{world?.label || '世界待提供'}</strong>
                            {world?.parentWorldId ? <span className="branch-tag">来源 {world.parentWorldId}</span> : null}
                            <span className="world-group-count">{items.length} 次运行</span>
                          </header>
                          <ul>
                            {items.map((run) => (
                              <li key={run.id}>
                                <button type="button" className={`run-node${selection.runId === run.id ? ' selected' : ''}`} onClick={() => choose(run.id)}>
                                  <span className={`node-dot ${run.status || 'unknown'}`} aria-hidden="true" />
                                  <span className="run-node-label">{run.label}</span>
                                  <span className={`run-status ${run.status || 'unknown'}`}>{statusLabel(run.status)}</span>
                                </button>
                              </li>
                            ))}
                          </ul>
                        </section>
                      );
                    })}
                  </div>
                ) : (
                  <StateBlock kind="empty" title="没有符合条件的运行" tight>调整搜索或状态筛选后重试。</StateBlock>
                )}
              </div>
            </>
          ) : (
            <StateBlock kind="unconfigured" title="还没有运行资料" actions={<button type="button" onClick={() => { select({ pageId: 'setup', sectionId: 'worlds' }); setOpen(false); }}>查看世界计划</button>}>
              平行世界可以先记录计划；每次实际执行会在这里作为独立运行出现。
            </StateBlock>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** 运行页主区：选中 run 后展示身份、世界与资源量。 */
export function RunDetail({ run, world, observations, selection, select }) {
  if (!run) {
    return (
      <>
        <div className="workspace-head"><h1>运行</h1><p>还没有选择运行</p></div>
        <div className="workspace-body">
          <StateBlock kind="empty" title="选择一次运行">从顶栏运行选择工作区挑选一条运行记录。</StateBlock>
        </div>
      </>
    );
  }
  const sourceWorld = world?.parentWorldId ? world.parentWorldId : '';
  return (
    <>
      <div className="workspace-head">
        <h1>{run.label}</h1>
        <p>{world?.label || '世界待提供'} · {run.versionId || '版本待提供'}</p>
        <span className="spacer" />
        <span className={`run-status ${run.status || 'unknown'}`}>{statusLabel(run.status)}</span>
      </div>
      <div className="workspace-body">
        <dl className="kv">
          <dt>运行编号</dt><dd className="mono">{run.id}</dd>
          <dt>所属世界</dt><dd>{world?.label || '世界待提供'}</dd>
          {world?.parentWorldId ? (<><dt>来源世界</dt><dd>{world.parentWorldId}</dd></>) : null}
          {world?.plannedSource ? (<><dt>来源完整点</dt><dd>{world.plannedSource}</dd></>) : null}
          <dt>资料截至</dt><dd>{run.dataThrough ? formatTime(run.dataThrough) : '尚无完整资料'}</dd>
          <dt>运行状态</dt><dd>{statusLabel(run.status)}</dd>
        </dl>

        <div style={{ marginTop: 16 }}>
          <div className="section-title"><h2>可用时点</h2><span>{observations.length} 个</span></div>
          {observations.length ? (
            <div className="obs-list">
              {observations.map((obs) => (
                <button key={obs.id} type="button" className={`obs-item${selection.observationId === obs.id ? ' selected' : ''}`} onClick={() => select({ observationId: obs.id })}>
                  <span className="obs-label">{obs.label}</span>
                  <span className="obs-time">{formatTime(obs.businessTime)}</span>
                  <span className="badge">{obs.availability === 'partial' ? '部分资料' : obs.availability === 'ready' ? '可读' : '待补'}</span>
                </button>
              ))}
            </div>
          ) : (
            <StateBlock kind="awaiting" title="这次运行还没有时点资料" tight>时点与结果随资料更新进入。</StateBlock>
          )}
        </div>
      </div>
    </>
  );
}
