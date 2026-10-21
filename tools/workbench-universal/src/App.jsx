import React, { useCallback, useMemo, useState } from 'react';
import { RUN_STATUS_LABELS, describeRevision } from './data.js';
import { SetupInsight, SetupMain, SetupWorkspace } from './SetupPanel.jsx';
import { RunLauncher } from './RunPanel.jsx';
import { TimePicker } from './TimePanel.jsx';
import { activeRunOf } from './data.js';
import { ObserveMain, useLayout } from './ObservePage.jsx';
import { AtlasMain } from './AtlasPage.jsx';
import { useWorkbench } from './store.js';
import { buildProposal, copyText, diffConfig, proposalText } from './proposal.js';

const BASE_PAGES = [{ id: 'setup', label: '实验设置' }];

export function App({ raw }) {
  const wb = useWorkbench(raw);
  const { data, version, runs, worlds, selection, select, collapsed, setCollapsed, draftFor } = wb;
  const pages = [...BASE_PAGES, ...(data.atlas ? [{ id: 'atlas', label: '图鉴' }] : data.panels.length ? [{ id: 'observe', label: '观察' }] : [])];
  const declared = data.workspace.pages.filter((id) => pages.some((page) => page.id === id));
  // 设计阶段（无配置版本、只有面板）直接进观察页，避免首屏停在空配置上。
  const designStage = !data.versions.length && data.panels.length > 0 && !data.atlas;
  const fallbackPage = designStage ? 'observe' : declared[0] || 'setup';
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

  const run = activeRunOf(data, version?.id) || null;
  const world = worlds.find((item) => item.id === (run?.worldId || selection.worldId)) || null;
  const observations = useMemo(() => (run ? data.observations.filter((obs) => obs.runId === run.id) : []), [data.observations, run]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <span className="brand">Society0</span>
          <span className="study-title" title={data.study.title}>{data.study.title}</span>
          <span className="study-revision">{describeRevision(data)}</span>
          <RunLauncher runs={runs} worlds={worlds} observations={data.observations} onOpenSetup={() => select({ pageId: 'setup' })} />
        </div>

        <div className="topbar-center">
          <nav className={`pages${pages.length > 5 ? ' scrollable' : ''}`} aria-label="工作台页面">
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
        </div>

        <div className="topbar-right">
          <TimePicker run={run} observations={observations} selection={selection} select={select} mode={data.workspace.timeMode} />
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
                {pageId === 'setup' ? <SetupWorkspace {...wb} /> : <ScopeSidebar run={run} world={world} observations={observations} selection={selection} select={select} />}
              </div>
            </div>
          )}
        </aside>

        <div className={`main${pageId === 'setup' ? '' : ' no-insight'}`}>
          <section className="panel workspace" aria-label="工作区">
            {pageId === 'setup' ? (
              <SetupMain {...wb} proposalText={proposalText(proposal)} copyState={copyState} onCopy={copy} />
            ) : pageId === 'atlas' ? (
              <AtlasMain data={data} selection={selection} select={select} />
            ) : (
              <ObserveMain data={data} selection={selection} select={select} layout={layout} onLayout={setLayout} />
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

function ScopeSidebar({ run, world, observations, selection, select }) {
  if (!run) {
    return <p className="side-empty">还没有运行资料。世界计划可以先在实验设置里核对。</p>;
  }
  const current = observations.find((obs) => obs.id === selection.observationId) || observations[0] || null;
  return (
    <>
      <div className="side-group">
        <h3>观察范围</h3>
        <dl className="side-kv">
          <dt>运行</dt><dd>{run.label}</dd>
          <dt>状态</dt><dd>{RUN_STATUS_LABELS[run.status] || RUN_STATUS_LABELS.unknown}</dd>
          <dt>世界</dt><dd>{world?.label || '世界待提供'}</dd>
          <dt>时点</dt><dd>{current ? current.label : '待提供'}</dd>
          <dt>资料截至</dt><dd>{run.dataThrough || '尚无完整资料'}</dd>
        </dl>
      </div>
      {observations.length > 1 ? (
        <div className="side-group">
          <h3>时点 · {observations.length}</h3>
          {observations.map((obs) => (
            <button
              key={obs.id}
              type="button"
              className="side-item side-sub"
              aria-current={obs.id === (current?.id || '')}
              onClick={() => select({ observationId: obs.id })}
            >
              <span className="label">{obs.label}</span>
              <span className="count">{obs.availability === 'partial' ? '部分' : ''}</span>
            </button>
          ))}
        </div>
      ) : null}
      <p className="side-empty">范围由资料决定；换运行请从顶部「运行」里检视。</p>
    </>
  );
}
