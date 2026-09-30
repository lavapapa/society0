import React, { useEffect, useState } from "react";
import {
  IconArrowsMaximize,
  IconBuilding,
  IconChartBar,
  IconChevronLeft,
  IconChevronRight,
  IconLayoutGrid,
  IconRobot,
  IconSearch,
  IconWorld,
  IconX,
} from "@tabler/icons-react";
import { blankStudy, changeRequest, changesBetween, mainTabsFor, rightTabsFor, setAtPath, snapshotFor } from "./model";
import { ViewTabs } from "./views";
import { ConfigPanel } from "./ConfigPanel";

function readStudy() {
  const source = document.getElementById("society0-workbench-data")?.textContent;
  if (!source) return { data: blankStudy, error: null };
  try {
    const data = JSON.parse(source);
    if (!Array.isArray(data.versions) || data.versions.some((version) =>
      !version || typeof version.config !== "object" || !Array.isArray(version.entities) || !Array.isArray(version.runs)
    )) {
      throw new Error("数据需要包含 versions 数组。");
    }
    return { data, error: null };
  } catch (error) {
    return { data: blankStudy, error: error.message };
  }
}

const loaded = readStudy();
const labelForKind = {
  "llm-agent": "LLM Agent",
  "rule-agent": "规则主体",
  institution: "制度实体",
  overview: "实验视角",
};

function Empty({ title, children }) {
  return <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}</div>;
}

function ModuleCard({ module, context, focused, onFocus }) {
  return (
    <section className={`module ${module.wide ? "wide" : ""}`}>
      <header className="module-heading">
        <div>
          <h2>{module.title || "未命名模块"}</h2>
          {module.description && <p className="module-description">{module.description}</p>}
        </div>
        <button aria-label={focused ? "缩小模块" : "放大模块"} onClick={onFocus}>
          {focused ? <IconX size={16} /> : <IconArrowsMaximize size={16} />}
        </button>
      </header>
      <ViewTabs views={module.views || []} context={context} />
      {module.source && <footer className="module-source">资料来源：{module.source}</footer>}
    </section>
  );
}

function MechanismCard({ mechanism, context, focused, onFocus }) {
  const access = mechanism.access || "unknown";
  const status = {
    available: "当前可接触",
    unavailable: "当前不可接触",
    unknown: "适用性待确认",
  }[access] || "适用性待确认";
  return (
    <section className={`module mechanism ${mechanism.wide ? "wide" : ""}`}>
      <header className="module-heading">
        <div>
          <p className="eyebrow">FoV 机制</p>
          <h2>{mechanism.name || "未命名机制"}</h2>
        </div>
        <button aria-label={focused ? "缩小模块" : "放大模块"} onClick={onFocus}>
          {focused ? <IconX size={16} /> : <IconArrowsMaximize size={16} />}
        </button>
      </header>
      <span className={`access access-${access}`}>{status}</span>
      {mechanism.description && <p className="mechanism-description">{mechanism.description}</p>}
      {mechanism.reason && <p className="mechanism-reason">{mechanism.reason}</p>}
      {mechanism.source && <p className="module-source">判断依据：{mechanism.source}</p>}
      <ViewTabs views={mechanism.views || []} context={context} />
    </section>
  );
}

function SessionPanel({ sessions, hasSnapshot }) {
  const [sessionId, setSessionId] = useState(sessions[0]?.id || "");
  const session = sessions.find((item) => item.id === sessionId) || sessions[0];
  if (!hasSnapshot) return <Empty title="本视角尚未整理会话资料">请核对当前实验条件、tick 和主体对应的原始记录。</Empty>;
  if (!session) return <Empty title="此 tick 没有记录会话">可以切换 tick，查看该主体其他时间的记录。</Empty>;
  return (
    <>
      <div className="session-controls">
        <label htmlFor="session-select">本 tick 的会话</label>
        {sessions.length > 1 ? (
          <select id="session-select" value={session.id} onChange={(event) => setSessionId(event.target.value)}>
            {sessions.map((item, index) => <option key={item.id} value={item.id}>{item.label || `第 ${index + 1} 次交互`}</option>)}
          </select>
        ) : <strong>{session.label || "第 1 次交互"}</strong>}
      </div>
      {session.description && <p className="session-description">{session.description}</p>}
      <div className="agent-transcript">
        {(session.events || []).map((event, index) => (
          <section className={`chat-event event-${event.kind || "observation"}`} key={event.id || index}>
            <div className="chat-event-label">
              <span>{event.title || {
                input: "输入",
                output: "输出",
                tool: "动作或工具",
                observation: "观察记录",
                error: "错误",
              }[event.kind] || "记录"}</span>
              {event.time && <time>{event.time}</time>}
            </div>
            {event.text && <p>{event.text}</p>}
            {event.data !== undefined && <pre className="tool-output">{JSON.stringify(event.data, null, 2)}</pre>}
            {event.source && <small>来源：{event.source}</small>}
          </section>
        ))}
        {!session.events?.length && <Empty title="这次会话没有可展示的记录" />}
      </div>
    </>
  );
}

function Workspace({ snapshot, entity, context, tabs, version, config, onConfigChange }) {
  const [tabId, setTabId] = useState(tabs[0]?.id || "");
  const [focus, setFocus] = useState(null);
  const tab = tabs.find((item) => item.id === tabId) || tabs[0];
  const modules = tab?.id === "mechanisms" ? snapshot?.mechanisms || [] : tab?.modules || [];
  const shown = focus ? modules.filter((item) => item.id === focus) : modules;
  return (
    <main className="workspace" id="main-content">
      <div className="workspace-heading">
        <div className="scope-title">
          <h1>{entity?.name || "选择观察对象"}</h1>
          <p>{entity ? labelForKind[entity.kind] || entity.kind || "观察对象" : "从左侧选择一个主体或实验视角"}</p>
        </div>
        {focus && <button className="focus-exit" onClick={() => setFocus(null)}>返回全部模块</button>}
      </div>
      {tabs.length > 0 && (
        <div className="workspace-tabs" role="tablist" aria-label="主体视图">
          {tabs.map((item) => (
            <button key={item.id} role="tab" aria-selected={tab?.id === item.id}
              className={tab?.id === item.id ? "active" : ""} onClick={() => { setTabId(item.id); setFocus(null); }}>
              {item.title}
            </button>
          ))}
        </div>
      )}
      {tab?.id === "mechanisms" && (
        <p className="view-explainer">这里展示当前主体在此 tick 可接触的 FoV 机制。机制说明不代表本次会话实际注入的资料。</p>
      )}
      {tab?.id === "config" && <ConfigPanel version={version} entity={entity} config={config} onChange={onConfigChange} />}
      {tab?.id !== "config" &&
      <div className={`module-grid ${focus ? "focused" : ""}`}>
        {shown.map((item) => tab?.id === "mechanisms" ? (
          <MechanismCard key={item.id} mechanism={item} context={context}
            focused={focus === item.id} onFocus={() => setFocus(focus === item.id ? null : item.id)} />
        ) : (
          <ModuleCard key={item.id} module={item} context={context}
            focused={focus === item.id} onFocus={() => setFocus(focus === item.id ? null : item.id)} />
        ))}
        {modules.length === 0 && (
          <Empty title={tab?.id === "mechanisms" ? "尚无已核实的 FoV 机制" : "这个视图还没有内容"}>
            {tab?.id === "mechanisms" ? "请根据实验定义和当前状态填写机制与适用条件。" : "选择其他视图，或由创建实验的 agent 补充分析。"}
          </Empty>
        )}
        {!tab && <Empty title="尚未生成观察视图">选择有实验资料的主体和 tick。</Empty>}
      </div>}
    </main>
  );
}

function Insight({ snapshot, tabs, context, changes }) {
  const [tabId, setTabId] = useState(tabs[0]?.id || "");
  const tab = tabs.find((item) => item.id === tabId) || tabs[0];
  return (
    <aside className="insight">
      <div className="insight-tabs" role="tablist" aria-label="相关记录">
        {tabs.map((item) => (
          <button key={item.id} role="tab" aria-selected={tab?.id === item.id}
            className={tab?.id === item.id ? "active" : ""} onClick={() => setTabId(item.id)}>{item.title}</button>
        ))}
      </div>
      <div className="insight-body">
        {tab?.id === "sessions" ? <SessionPanel sessions={snapshot?.sessions || []} hasSnapshot={Boolean(snapshot)} /> : tab?.id === "changes" ? (
          <div className="change-list">
            <p>此处的修改尚未写入实验文件。复制顶部的变更请求，发送给 agent 后再由它核对和创建新版本。</p>
            {changes.length ? changes.map((change) => <div className="change-item" key={change.path}>
              <strong>{({ add: "新增", remove: "删除", replace: "修改" })[change.op]}：{change.path || "完整配置"}</strong>
              <small>原值：{JSON.stringify(change.before) ?? "未设置"}</small>
              <small>拟改为：{JSON.stringify(change.after) ?? "删除"}</small>
            </div>) : <Empty title="尚无待发送变更">在中间的“配置检查”中修改字段，变更会显示在这里。</Empty>}
          </div>
        ) : (
          (tab?.modules || []).map((module) => (
            <section className="side-module" key={module.id}>
              <h2>{module.title}</h2>
              {module.description && <p>{module.description}</p>}
              <ViewTabs views={module.views || []} context={context} />
              {module.source && <p className="module-source">资料来源：{module.source}</p>}
            </section>
          ))
        )}
      </div>
    </aside>
  );
}

export function App() {
  const { data, error } = loaded;
  const [versionId, setVersionId] = useState(data.versions[0]?.id || "");
  const [runId, setRunId] = useState(data.versions[0]?.runs?.[0]?.id || "");
  const [entityId, setEntityId] = useState(data.versions[0]?.entities?.[0]?.id || "");
  const [tickId, setTickId] = useState(data.versions[0]?.runs?.[0]?.ticks?.[0]?.id || "");
  const [drafts, setDrafts] = useState({});
  const [copyState, setCopyState] = useState("");
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const version = data.versions.find((item) => item.id === versionId);
  const runs = version?.runs || [];
  const entities = version?.entities || [];
  const config = drafts[versionId] || version?.config || {};
  const changes = version ? changesBetween(version.config, config) : [];
  const hasDraftChanges = Object.keys(drafts).some((id) => {
    const source = data.versions.find((item) => item.id === id);
    return source && changesBetween(source.config, drafts[id]).length > 0;
  });
  useEffect(() => {
    if (!hasDraftChanges) return undefined;
    const warn = (event) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasDraftChanges]);
  const run = runs.find((item) => item.id === runId);
  const entity = entities.find((item) => item.id === entityId);
  const tick = run?.ticks?.find((item) => item.id === tickId);
  const tickIndex = run?.ticks?.findIndex((item) => item.id === tickId) ?? -1;
  const snapshot = snapshotFor(data, versionId, runId, tickId, entityId);
  const mainTabs = mainTabsFor(snapshot, entity, Boolean(version));
  const rightTabs = rightTabsFor(snapshot, entity, Boolean(version));
  const context = { version, run, tick, entity, snapshot };
  const key = `${versionId}:${runId}:${tickId}:${entityId}`;
  const groups = entities.filter((item) =>
    item.name?.toLowerCase().includes(query.toLowerCase())
  ).reduce((result, item) => {
    const group = item.group || labelForKind[item.kind] || "其他主体";
    (result[group] ||= []).push(item);
    return result;
  }, {});
  const onConfigChange = (path, value) => {
    setDrafts((current) => ({ ...current, [versionId]: setAtPath(current[versionId] || version.config, path, value) }));
    setCopyState("");
  };
  const copyChanges = async () => {
    const message = JSON.stringify(changeRequest(version, changes), null, 2);
    try {
      await navigator.clipboard.writeText(message);
      setCopyState("已复制；请粘贴发送给 agent。复制本身不会修改实验文件。");
    } catch {
      setCopyState(message);
    }
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">跳到主体视图</a>
      <header className="topbar">
        <div className="brand"><IconLayoutGrid size={22} /> Society0 工作台</div>
        <div className="global-controls">
          <span className="experiment" title={data.study?.question || ""}>{data.study?.title || "未命名实验"}</span>
          <span className="snapshot-badge">{run ? "已保存试运行" : "配置检查"}</span>
          {data.versions.length > 0 && <label>配置版本
            <select aria-label="配置版本" value={versionId} onChange={(event) => {
              const next = data.versions.find((item) => item.id === event.target.value);
              setVersionId(event.target.value); setRunId(next?.runs?.[0]?.id || ""); setTickId(next?.runs?.[0]?.ticks?.[0]?.id || ""); setEntityId(next?.entities?.[0]?.id || ""); setCopyState("");
            }}>{data.versions.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}</select>
          </label>}
          {version && <label>试运行
              <select aria-label="试运行" value={runId} onChange={(event) => {
                const nextRun = runs.find((item) => item.id === event.target.value);
                setRunId(event.target.value);
                setTickId(nextRun?.ticks?.[0]?.id || "");
              }}>
                <option value="">配置视图</option>
                {runs.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
              </select>
            </label>
          }
          {run?.ticks?.length > 0 && (
            <div className="tick-controls">
              <button aria-label="查看上一个已保存 tick" disabled={tickIndex <= 0} onClick={() => setTickId(run.ticks[tickIndex - 1].id)}><IconChevronLeft size={16} /></button>
              <label>查看 tick
                <select aria-label="Tick" value={tickId} onChange={(event) => setTickId(event.target.value)}>
                  {run.ticks.map((item) => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}
                </select>
              </label>
              <button aria-label="查看下一个已保存 tick" disabled={tickIndex < 0 || tickIndex >= run.ticks.length - 1}
                onClick={() => setTickId(run.ticks[tickIndex + 1].id)}><IconChevronRight size={16} /></button>
            </div>
          )}
          <button className="copy-changes" disabled={!changes.length} onClick={copyChanges}>复制变更{changes.length ? ` (${changes.length})` : ""}</button>
        </div>
      </header>
      {data.study?.question && <p className="study-question">研究问题：{data.study.question}</p>}
      {version && <p className="workbench-status">{run ? `正在查看 ${version.name || version.id} 下的 ${run.name || run.id}；结果来自已保存资料。` : `正在检查 ${version.name || version.id} 的配置；页面内修改不会写入实验文件。`}{runs.length ? ` 本版本共 ${runs.length} 次试运行。` : " 本版本尚无试运行。"}</p>}
      {copyState && <div className="copy-status" role="status">{copyState.startsWith("{") ? <><p>浏览器未允许自动复制，请手动复制下方请求并发给 agent：</p><textarea readOnly value={copyState} rows={7} onFocus={(event) => event.target.select()} /></> : copyState}</div>}
      {error && <div className="data-error" role="alert">工作台数据无法读取：{error} 请检查 HTML 中的 society0-workbench-data。</div>}
      <div className={`shell ${collapsed ? "collapsed" : ""}`}>
        <aside className="sidebar">
          <div className="side-heading"><span>观察范围</span>
            <button aria-label={collapsed ? "展开侧栏" : "折叠侧栏"} onClick={() => setCollapsed(!collapsed)}>
              <IconLayoutGrid size={16} />
            </button>
          </div>
          {entities.length > 0 && (
            <label className="search"><IconSearch size={16} />
              <input aria-label="搜索主体" placeholder="搜索主体" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
          )}
          <nav className="actors" aria-label="主体与实验视角">
            {Object.entries(groups).map(([group, entries]) => (
              <div key={group}>
                <div className="actor-label">{group}<span>{entries.length}</span></div>
                {entries.map((item) => {
                  const Icon = item.kind === "llm-agent" ? IconRobot : item.kind === "overview" ? IconWorld : item.kind === "institution" ? IconBuilding : IconChartBar;
                  return <button key={item.id} title={item.name} className={entityId === item.id ? "selected" : ""}
                    onClick={() => setEntityId(item.id)}><Icon size={18} /><span>{item.name}<small>{item.subtitle || labelForKind[item.kind] || ""}</small></span></button>;
                })}
              </div>
            ))}
            {entities.length === 0 && <Empty title="尚无观察对象">配置版本可加入 Agent、制度实体和实验整体视角。</Empty>}
            {entities.length > 0 && !Object.keys(groups).length && <Empty title="没有匹配的主体" />}
          </nav>
        </aside>
        <div className={`content ${rightTabs.length ? "" : "without-insight"}`}>
          <Workspace key={`workspace:${key}`} snapshot={snapshot} entity={entity} context={context} tabs={mainTabs} version={version} config={config} onConfigChange={onConfigChange} />
          {rightTabs.length > 0 && <Insight key={`insight:${key}`} snapshot={snapshot} tabs={rightTabs} context={context} changes={changes} />}
        </div>
      </div>
    </div>
  );
}
