import React, { useCallback, useMemo, useState } from 'react';
import { RUN_STATUS_LABELS, describeRevision } from './data.js';
import { SetupInsight, SetupMain, SetupWorkspace } from './SetupPanel.jsx';
import { RunDetail, RunPicker } from './RunPanel.jsx';
import { TimeControl } from './TimePanel.jsx';
import { ObserveMain, useLayout } from './ObservePage.jsx';
import { AtlasMain } from './AtlasPage.jsx';
import { useWorkbench } from './store.js';
import { buildProposal, copyText, diffConfig, proposalText } from './proposal.js';

const BASE_PAGES = [
  { id: 'setup', label: '实验设置' },
  { id: 'runs', label: '运行' },
];

export function App({ raw }) {
  const wb = useWorkbench(raw);
  const { data, version, runs, worlds, selection, select, collapsed, setCollapsed, draftFor } = wb;
  const pages = [...BASE_PAGES, ...(data.atlas ? [{ id: 'atlas', label: '图鉴' }] : data.panels.length ? [{ id: 'observe', label: '观察' }] : [])];
  const declared = data.workspace.pages.filter((id) => pages.some((page) => page.id === id));
  const fallbackPage = declared[0] || 'setup';
  const pageId = pages.some((page) => page.id === selection.pageId) ? selection.pageId : fallbackPage;
  const [layout, setLayout] = useLayout(data.study.id);
  const [mobileSidebar, setMobileSidebar] = useState(false);

  const source = version?.configSource || '';
  const draft = draftFor(source);
  const changes = useMemo(
    () => (draft ? diffConfig(version?.config || {}, draft.value?.config ?? {}, version?.configFields || []) : []),
    [draft, version],
  );
  const proposal = useMemo(
    () => (changes.length ? buildProposal({ version, configSource: source, changes }) : null),
    [changes, version, source],
  );

  const [copyState, setCopyState] = useState('idle');
  const copy = useCallback(async () => {
    if (!proposal) return;
    const ok = await copyText(proposalText(proposal));
    setCopyState(ok ? 'copied' : 'manual');
  }, [proposal]);

  const run = runs.find((item) => item.id === selection.runId) || null;
  const world = worlds.find((item) => item.id === (run?.worldId || selection.worldId)) || null;
  const observations = useMemo(() => (run ? data.observations.filter((obs) => obs.runId === run.id) : []), [data.observations, run]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <span className="brand">Society0</span>
          <span className="study-title" title={data.study.title}>{data.study.title}</span>
          <span className="study-revision">{describeRevision(data)}</span>
        </div>
        <div className="topbar-center">
          <nav className="pages" aria-label="工作台页面">
            <button
              type="button"
              className="mobile-nav"
              aria-label="打开观察范围"
              aria-expanded={mobileSidebar}
              onClick={() => setMobileSidebar((value) => !value)}
            >☰</button>
            {pages.map((page) => (
              <button key={page.id} type="button" aria-current={pageId === page.id ? 'page' : undefined} onClick={() => select({ pageId: page.id })}>
                {page.label}
              </button>
            ))}
          </nav>
          {run ? (
            <TimeControl observations={observations} selection={selection} select={select} modes={data.workspace.timeModes} />
          ) : null}
        </div>
        <div className="topbar-right">
          <RunPicker runs={runs} worlds={worlds} selection={selection} select={select} version={version} />
          {data.versions.length > 1 ? (
            <label className="version-select">
              <select aria-label="配置版本" value={version?.id || ''} onChange={(event) => select({ versionId: event.target.value })}>
                {data.versions.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
          ) : null}
          {pageId === 'setup' ? (
            <button
              type="button"
              className="copy-button"
              data-has-draft={changes.length > 0}
              disabled={!changes.length}
              onClick={copy}
              title={changes.length ? '复制参数变更请求' : '当前没有待发送变更'}
            >
              {copyState === 'copied' ? '已复制' : copyState === 'manual' ? '请手动复制' : '复制变更'}
            </button>
          ) : null}
        </div>
      </header>

      <div className={`shell${collapsed ? ' collapsed' : ''}${mobileSidebar ? ' mobile-sidebar' : ''}`}>
        <aside className="panel sidebar" aria-label="实验配置">
          <div className="sidebar-head">
            <strong>{collapsed ? '' : '观察范围'}</strong>
            <button type="button" className="icon-button" aria-label={collapsed ? '展开左栏' : '收起左栏'} onClick={() => setCollapsed(!collapsed)}>
              {collapsed ? '»' : '«'}
            </button>
          </div>
          {collapsed ? null : (
            <div className="sidebar-body">
              <div onClick={() => { if (window.innerWidth <= 620) setMobileSidebar(false); }}>
                {pageId === 'setup' ? <SetupWorkspace {...wb} /> : <RunSidebar runs={runs} selection={selection} select={select} />}
              </div>
            </div>
          )}
        </aside>

        <div className={`main${pageId === 'setup' ? '' : ' no-insight'}`}>
          <section className="panel workspace" aria-label="工作区">
            {pageId === 'setup' ? (
              <SetupMain {...wb} proposalText={proposalText(proposal)} copyState={copyState} onCopy={copy} />
            ) : pageId === 'atlas' ? (
              <AtlasMain data={data} selection={selection} select={select} layout={layout} onLayout={setLayout} />
            ) : pageId === 'observe' ? (
              <ObserveMain data={data} selection={selection} select={select} layout={layout} onLayout={setLayout} />
            ) : (
              <RunDetail run={run} world={world} observations={observations} selection={selection} select={select} />
            )}
          </section>
          {pageId === 'setup' ? (
            <aside className="panel insight" aria-label="变更与记录">
              <SetupInsight changes={changes} proposalText={proposalText(proposal)} copyState={copyState} onCopy={copy} />
            </aside>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function RunSidebar({ runs, selection, select }) {
  if (!runs.length) {
    return <p className="side-empty">还没有运行资料。世界计划可以先在实验设置里核对。</p>;
  }
  return (
    <div className="side-group">
      <h3>运行 · {runs.length}</h3>
      {runs.map((run) => (
        <button key={run.id} type="button" className="side-item" aria-current={selection.runId === run.id} onClick={() => select({ runId: run.id })}>
          <span className="label">{run.label}</span>
          <span className={`status-text ${run.status || 'unknown'}`}>{RUN_STATUS_LABELS[run.status] || RUN_STATUS_LABELS.unknown}</span>
        </button>
      ))}
    </div>
  );
}
