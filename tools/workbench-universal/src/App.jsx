import React, { useCallback, useMemo, useState } from 'react';
import { RUN_STATUS_LABELS, configSections, describeRevision, formatTime } from './data.js';
import { SetupInsight, SetupMain, SetupWorkspace } from './SetupPanel.jsx';
import { StateBlock } from './ui.jsx';
import { useWorkbench } from './store.js';
import { buildProposal, copyText, diffConfig, proposalText } from './proposal.js';

const PAGES = [
  { id: 'setup', label: '实验设置' },
  { id: 'runs', label: '运行' },
];

export function App({ raw }) {
  const wb = useWorkbench(raw);
  const { data, version, runs, selection, select, collapsed, setCollapsed, draftFor } = wb;
  const pageId = PAGES.some((page) => page.id === selection.pageId) ? selection.pageId : 'setup';

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
            {PAGES.map((page) => (
              <button key={page.id} type="button" aria-current={pageId === page.id ? 'page' : undefined} onClick={() => select({ pageId: page.id })}>
                {page.label}
              </button>
            ))}
          </nav>
        </div>
        <div className="topbar-right">
          {data.versions.length > 1 ? (
            <label className="version-select">
              <select aria-label="配置版本" value={version?.id || ''} onChange={(event) => select({ versionId: event.target.value })}>
                {data.versions.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
          ) : null}
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

      <div className={`shell${collapsed ? ' collapsed' : ''}`}>
        <aside className="panel sidebar" aria-label="实验配置">
          <div className="sidebar-head">
            <strong>{collapsed ? '' : '观察范围'}</strong>
            <button type="button" className="icon-button" aria-label={collapsed ? '展开左栏' : '收起左栏'} onClick={() => setCollapsed(!collapsed)}>
              {collapsed ? '»' : '«'}
            </button>
          </div>
          {collapsed ? null : (
            <div className="sidebar-body">
              {pageId === 'setup' ? <SetupWorkspace {...wb} /> : <RunSidebar runs={runs} selection={selection} select={select} />}
            </div>
          )}
        </aside>

        <div className={`main${pageId === 'setup' ? '' : ' no-insight'}`}>
          <section className="panel workspace" aria-label="工作区">
            {pageId === 'setup' ? (
              <SetupMain {...wb} proposal={proposal} proposalText={proposalText(proposal)} copyState={copyState} onCopy={copy} />
            ) : (
              <RunsMain runs={runs} worlds={wb.worlds} selection={selection} select={select} />
            )}
          </section>
          {pageId === 'setup' ? (
            <aside className="panel insight" aria-label="变更与记录">
              <SetupInsight changes={changes} proposal={proposal} proposalText={proposalText(proposal)} copyState={copyState} onCopy={copy} />
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

function RunsMain({ runs, worlds, selection, select }) {
  if (!runs.length) {
    return (
      <>
        <div className="workspace-head"><h1>运行</h1><p>还没有可查看的运行</p></div>
        <div className="workspace-body">
          <StateBlock kind="unconfigured" title="还没有运行资料">
            运行出现后，这里会按世界和状态列出每一次运行。可以先在实验设置里核对平行世界计划。
          </StateBlock>
        </div>
      </>
    );
  }
  return (
    <>
      <div className="workspace-head"><h1>运行</h1><p>{runs.length} 次运行</p></div>
      <div className="workspace-body">
        <div className="grid-cards">
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              className={`run-card${selection.runId === run.id ? ' selected' : ''}`}
              aria-current={selection.runId === run.id}
              onClick={() => select({ runId: run.id })}
            >
              <span className="run-world">{worlds.find((world) => world.id === run.worldId)?.label || '世界待提供'}</span>
              <strong>{run.label}</strong>
              <span className={`run-status ${run.status || 'unknown'}`}>{RUN_STATUS_LABELS[run.status] || RUN_STATUS_LABELS.unknown}</span>
              <small>{run.dataThrough ? `资料截至 ${formatTime(run.dataThrough)}` : '尚无完整资料'}</small>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

export { configSections };
