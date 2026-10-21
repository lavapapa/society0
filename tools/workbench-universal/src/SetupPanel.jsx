import React, { useMemo, useState } from 'react';
import { configSections, entityGroups, formatTime, hasContent } from './data.js';
import { Badge, Notice, SectionTitle, StateBlock } from './ui.jsx';
import { copyText, diffConfig, valueAt, withValueAt } from './proposal.js';

/** 配置字段编辑：值写入版本草稿，不修改资料本身。 */
function ConfigFields({ fields, baseConfig, draftConfig, onChange }) {
  if (!fields.length) {
    return (
      <StateBlock kind="awaiting" title="参数尚未提供" tight>
        这个配置分区的字段还没有随资料一起给出。可以先把需要调整的内容和 Agent 讨论，资料更新后再在这里核对。
      </StateBlock>
    );
  }
  return (
    <div className="field-list">
      {fields.map((field) => {
        const current = valueAt(draftConfig, field.path);
        const original = valueAt(baseConfig, field.path);
        const dirty = JSON.stringify(current) !== JSON.stringify(original);
        return (
          <div className="field" key={field.path}>
            <div className="field-label">
              <b>{field.label || field.path}</b>
              {field.path ? <code>{field.path}</code> : null}
              {field.description ? <small>{field.description}</small> : null}
            </div>
            <div className="field-value">
              {field.type === 'boolean' ? (
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                  <input type="checkbox" checked={Boolean(current)} onChange={(event) => onChange(field.path, event.target.checked)} />
                  {current ? '已启用' : '未启用'}
                </label>
              ) : field.type === 'number' ? (
                <input
                  type="number"
                  value={current ?? ''}
                  onChange={(event) => onChange(field.path, event.target.value === '' ? null : Number(event.target.value))}
                />
              ) : field.options ? (
                <select value={current ?? ''} onChange={(event) => onChange(field.path, event.target.value)}>
                  {field.options.map((option) => {
                    const value = typeof option === 'string' ? option : option.value;
                    const label = typeof option === 'string' ? option : option.label;
                    return <option key={value} value={value}>{label}</option>;
                  })}
                </select>
              ) : field.multiline ? (
                <textarea rows={3} value={current ?? ''} onChange={(event) => onChange(field.path, event.target.value)} />
              ) : (
                <input type="text" value={current ?? ''} onChange={(event) => onChange(field.path, event.target.value)} />
              )}
              {dirty ? <span className="original">原值：{formatValue(original)}</span> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function formatValue(value) {
  if (value === undefined) return '未设置';
  if (value === null) return '空';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function PluginSection({ plugins, study }) {
  const [copied, setCopied] = useState(false);
  if (!plugins.length) {
    const hint = `我想为「${study.title}」准备实验机制。请先帮我确认这次实验需要哪些插件（环境、主体认知、记忆、市场等），再补充各自的参数。`;
    return (
      <StateBlock
        kind="unconfigured"
        title="还没有确定实验需要的机制"
        actions={
          <button
            type="button"
            onClick={async () => {
              const ok = await copyText(hint);
              setCopied(ok ? '已复制' : '复制失败，请手动选择文本');
              setTimeout(() => setCopied(false), 2400);
            }}
          >
            {copied || '复制讨论要点'}
          </button>
        }
      >
        先和 Agent 讨论这次实验需要哪些机制，例如环境、主体认知、记忆或市场规则。资料更新后，插件与参数会出现在这里。
      </StateBlock>
    );
  }
  return (
    <div className="plugin-list">
      {plugins.map((plugin) => (
        <article className="plugin" key={plugin.id}>
          <h4>{plugin.label}</h4>
          <Badge state={plugin.availability}>{labelFor(plugin.availability)}</Badge>
          {plugin.description ? <p>{plugin.description}</p> : null}
          <div className="plugin-meta">
            {plugin.category ? <span>{plugin.category}</span> : null}
            <span className="mono">{plugin.id}</span>
            {plugin.parameters ? <span>{plugin.parameters} 个参数</span> : <span>参数待补充</span>}
            {plugin.dependsOn?.length ? <span>依赖：{plugin.dependsOn.join('、')}</span> : null}
          </div>
        </article>
      ))}
    </div>
  );
}

function labelFor(state) {
  return { unconfigured: '尚未配置', awaiting: '待补充', ready: '已就绪', partial: '部分资料', error: '读取失败', empty: '无内容' }[state] || '待补充';
}

function WorldSection({ worlds, runs, onOpenRuns }) {
  if (!worlds.length) {
    return (
      <StateBlock kind="unconfigured" title="还没有世界计划">
        平行世界用来对照不同参数或政策条件。先和 Agent 确定要比较的世界，计划会出现在这里；运行资料可以在之后补充。
      </StateBlock>
    );
  }
  return (
    <div className="world-list">
      {worlds.map((world) => {
        const worldRuns = runs.filter((run) => run.worldId === world.id);
        return (
          <article className={`world-card${world.parentWorldId ? ' child' : ''}`} key={world.id}>
            <header>
              {world.parentWorldId ? <span className="branch-mark" aria-hidden="true">↳</span> : null}
              <strong>{world.label}</strong>
              <Badge state={world.availability}>{labelFor(world.availability)}</Badge>
              <span className="world-run-count">
                {worldRuns.length ? (
                  <button type="button" className="link-button" onClick={() => onOpenRuns(world.id)}>{worldRuns.length} 次运行</button>
                ) : (
                  <span>尚无运行</span>
                )}
              </span>
            </header>
            <div className="world-meta">
              <span className="mono">{world.id}</span>
              {world.parentWorldId ? <span>来源：{world.parentWorldId}</span> : <span>独立世界</span>}
              {world.plannedSource ? <span>计划来源：{world.plannedSource}</span> : <span>来源待补</span>}
            </div>
            {world.overrides && Object.keys(world.overrides).length ? (
              <div className="world-overrides">
                参数差异：
                {Object.entries(world.overrides).map(([key, value]) => (
                  <span key={key} style={{ marginLeft: 6 }}>
                    <code>{key}</code> = {String(value)}
                  </span>
                ))}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

export function SetupWorkspace({ data, version, runs, selection, select, draftFor, setDraft }) {
  const sections = useMemo(() => configSections(data, version), [data, version]);
  const sectionId = sections.some((item) => item.id === selection.sectionId) ? selection.sectionId : 'plugins';
  const groups = useMemo(() => entityGroups(data, version), [data, version]);
  const [entityTab, setEntityTab] = useState('overview');

  const source = version?.configSource || '';
  const baseConfig = version?.config || {};
  const draft = draftFor(source);
  const draftConfig = draft?.value?.config ?? baseConfig;
  const changes = useMemo(() => diffConfig(baseConfig, draftConfig, version?.configFields || []), [baseConfig, draftConfig, version]);

  const updateField = (path, value) => {
    const next = withValueAt(draftConfig, path, value);
    setDraft(source, { config: next, base: baseConfig });
  };

  const entity = data.entities.find((item) => item.id === selection.entityId) || null;

  return (
    <>
      {sections.map((section) => (
        <button
          key={section.id}
          type="button"
          className="side-item"
          aria-current={sectionId === section.id}
          onClick={() => select({ sectionId: section.id, entityId: '' })}
        >
          <span className="index">{section.id === 'plugins' ? 1 : section.id === 'worlds' ? 2 : '·'}</span>
          <span className="label">{section.label}</span>
          <span className={`dot ${sectionState(section.id, data, changes)}`} />
        </button>
      ))}
      <div className="side-group">
        <h3>主体与对象</h3>
        {groups.length ? (
          groups.map((group) => (
            <div key={group.id}>
              <div className="side-empty" style={{ paddingBottom: 2 }}>{group.label} · {group.count}</div>
              {data.entities
                .filter((item) => (item.group || '未分组') === group.id)
                .slice(0, 30)
                .map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="side-item"
                    aria-current={selection.entityId === item.id}
                    onClick={() => select({ entityId: item.id, pageId: 'setup' })}
                  >
                    <span className="label">{item.label}</span>
                    <span className="count">{item.kind || '对象'}</span>
                  </button>
                ))}
            </div>
          ))
        ) : (
          <p className="side-empty">主体尚未配置。等 Agent 补入身份与分组后，这里会出现目录。</p>
        )}
      </div>
      <div style={{ display: 'none' }}>{entityTab}</div>
    </>
  );
}

function sectionState(sectionId, data, changes) {
  if (sectionId === 'plugins') return data.plugins.length ? 'ready' : 'awaiting';
  if (sectionId === 'worlds') return data.worlds.length ? 'ready' : 'awaiting';
  return changes.length ? 'awaiting' : '';
}

function PreparationChecklist({ data }) {
  const rows = [
    { id: 'plugins', label: '插件与参数', ready: data.plugins.length > 0, note: '实验里存在哪些机制' },
    { id: 'worlds', label: '平行世界', ready: data.worlds.length > 0, note: '要对照的条件与参数差异' },
    { id: 'entities', label: '主体与对象', ready: data.entities.length > 0, note: '谁在实验里行动' },
    { id: 'runs', label: '运行记录', ready: data.runs.length > 0, note: '每次实际执行' },
    { id: 'results', label: '观察结果', ready: data.observations.length > 0 || data.datasets.length > 0, note: '时点、指标与正文' },
  ];
  const ready = rows.filter((row) => row.ready).length;
  if (ready === rows.length) return null;
  return (
    <div className="card prep-card">
      <div className="card-head">
        <h3>资料准备</h3>
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 10 }}>{ready} / {rows.length} 就绪</span>
      </div>
      <ul className="prep-list">
        {rows.map((row) => (
          <li key={row.id} className={row.ready ? 'ready' : 'pending'}>
            <span className="prep-dot" aria-hidden="true" />
            <strong>{row.label}</strong>
            <span className="prep-note">{row.note}</span>
            <span className="prep-state">{row.ready ? '已就绪' : '待提供'}</span>
          </li>
        ))}
      </ul>
      <p className="prep-foot">资料由 Agent 随对话补入；每次更新后刷新页面即可看到进展。</p>
    </div>
  );
}

export function SetupMain({ data, version, runs, selection, select, draftFor, setDraft, proposalText, copyState, onCopy }) {
  const sections = useMemo(() => configSections(data, version), [data, version]);
  const sectionId = sections.some((item) => item.id === selection.sectionId) ? selection.sectionId : 'plugins';
  const section = sections.find((item) => item.id === sectionId) || sections[0];
  const source = version?.configSource || '';
  const baseConfig = version?.config || {};
  const draft = draftFor(source);
  const draftConfig = draft?.value?.config ?? baseConfig;
  const changes = useMemo(() => diffConfig(baseConfig, draftConfig, version?.configFields || []), [baseConfig, draftConfig, version]);

  const entity = data.entities.find((item) => item.id === selection.entityId) || null;

  if (entity) {
    return <EntityInspector entity={entity} data={data} onBack={() => select({ entityId: '' })} />;
  }

  return (
    <>
      <div className="workspace-head">
        <h1>{section.label}</h1>
        <p>{section.id === 'plugins' ? '决定实验里存在哪些机制' : section.id === 'worlds' ? '计划要对照的世界与参数' : '当前版本的实际配置'}</p>
        <span className="spacer" />
        {version?.configSource ? <span className="muted mono" style={{ fontSize: 10 }}>{version.configSource}</span> : null}
      </div>
      <div className="workspace-body">
        {section.id === 'plugins' ? (
          <>
            <p className="section-intro">
              插件决定环境里存在哪些机制。这里的组件开关只影响页面呈现，仿真插件是否启用以实际配置为准。
            </p>
            {version?.configFields?.length ? (
              <>
                <SectionTitle title="参数" note={changes.length ? `${changes.length} 处未发送修改` : '修改后可在右侧复制请求'} />
                <ConfigFields fields={version.configFields} baseConfig={baseConfig} draftConfig={draftConfig} onChange={(path, value) => setDraft(source, { config: withValueAt(draftConfig, path, value), base: baseConfig })} />
                <div style={{ height: 16 }} />
              </>
            ) : null}
            <SectionTitle title="已配置插件" note={data.plugins.length ? `${data.plugins.length} 项` : ''} />
            <PluginSection plugins={data.plugins} study={data.study} />
            <PreparationChecklist data={data} />
          </>
        ) : section.id === 'worlds' ? (
          <>
            <p className="section-intro">
              平行世界先记录计划，再补入运行。每个世界可以有多次运行；来源与参数差异随资料一起呈现。
            </p>
            <WorldSection worlds={data.worlds} runs={runs} onOpenRuns={(worldId) => select({ pageId: 'runs', worldId })} />
          </>
        ) : (
          <StateBlock kind="unconfigured" title={section.label}>
            这个分区还没有随资料提供内容。
          </StateBlock>
        )}
      </div>
    </>
  );
}

function EntityInspector({ entity, data, onBack }) {
  const records = data.documents.filter((doc) => doc.entityId === entity.id);
  return (
    <>
      <div className="workspace-head">
        <h1>{entity.label}</h1>
        <p>{entity.kind || '对象'} · {entity.group || '未分组'}</p>
        <span className="spacer" />
        <button type="button" onClick={onBack}>返回配置</button>
      </div>
      <div className="workspace-body">
        <SectionTitle title="身份与属性" note={entity.id} />
        <dl className="kv">
          {Object.entries(entity.attributes || {}).map(([key, value]) => (
            <React.Fragment key={key}>
              <dt>{key}</dt>
              <dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
            </React.Fragment>
          ))}
        </dl>
        <div style={{ marginTop: 16 }}>
          <SectionTitle title="相关资料" note={records.length ? `${records.length} 条` : ''} />
          {records.length ? (
            <div className="record-list">
              {records.map((doc) => (
                <div className="record-item" key={doc.id}>
                  <strong>{doc.title}</strong>
                  <small>{formatTime(doc.at)} · {doc.kind}</small>
                </div>
              ))}
            </div>
          ) : (
            <StateBlock kind="empty" title="这个对象还没有资料" tight>
              会话、配置或记录会在后续资料中出现。
            </StateBlock>
          )}
        </div>
      </div>
    </>
  );
}

export function SetupInsight({ changes, proposalText, copyState, onCopy }) {
  const [showText, setShowText] = useState(false);
  return (
    <>
      <div className="insight-head"><strong>待发送变更</strong></div>
      <div className="insight-body">
        <p className="change-intro">此处的修改尚未写入实验文件。复制请求发给 Agent，由它核对原值并创建新配置版本。</p>
        {changes.length ? (
          <>
            <div className="change-list">
              {changes.map((change) => (
                <div className="change-item" key={change.path}>
                  <div className="path">{change.op} {change.path}</div>
                  <div className="diff">
                    {change.before !== undefined ? <span className="before">{formatValue(change.before)}</span> : null}
                    <span className="after">{formatValue(change.after)}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="change-actions">
              <button type="button" onClick={onCopy}>{copyState === 'copied' ? '已复制请求' : '复制变更'}</button>
              <button type="button" onClick={() => setShowText((value) => !value)}>{showText ? '收起原文' : '查看原文'}</button>
            </div>
            {copyState === 'manual' ? <p className="copy-hint">剪贴板不可用。请从下方原文手动复制同一份完整请求。</p> : null}
            {showText || copyState === 'manual' ? (
              <div className="proposal-preview">
                <textarea readOnly value={proposalText} rows={12} aria-label="变更请求原文" />
              </div>
            ) : null}
          </>
        ) : (
          <StateBlock kind="empty" title="尚无待发送变更" tight>
            在配置检查中修改字段，变更会显示在这里。
          </StateBlock>
        )}
      </div>
    </>
  );
}

export { formatValue };
