import React, { useMemo, useRef, useState } from 'react';
import { PANEL_CATALOG, PANEL_GROUPS } from './catalog.js';
import { renderPanel } from './Panels.jsx';
import { StateBlock } from './ui.jsx';

/** 组件库页面：左侧分组导航，右侧按分组分区，每个组件一张带示例的卡片。 */
export function AtlasMain({ data, selection, select }) {
  const [group, setGroup] = useState('全部');
  const [query, setQuery] = useState('');
  const [dense, setDense] = useState(false);
  const [expanded, setExpanded] = useState(() => new Set());
  const sectionsRef = useRef({});

  const panelByType = useMemo(() => {
    const map = new Map();
    for (const panel of data.panels) if (!map.has(panel.type)) map.set(panel.type, panel);
    return map;
  }, [data.panels]);

  const entries = useMemo(() => {
    const text = query.trim().toLowerCase();
    return PANEL_CATALOG.filter((meta) => {
      if (group !== '全部' && meta.group !== group) return false;
      if (!text) return true;
      return `${meta.label} ${meta.type} ${meta.purpose} ${meta.group}`.toLowerCase().includes(text);
    });
  }, [group, query]);

  const visibleGroups = PANEL_GROUPS.filter((name) => entries.some((meta) => meta.group === name));
  const withSample = entries.filter((meta) => panelByType.has(meta.type)).length;

  const toggle = (type) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const jump = (name) => {
    setGroup(name);
    const target = name === '全部' ? null : sectionsRef.current[name];
    if (target) requestAnimationFrame(() => target.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  };

  return (
    <>
      <div className="workspace-head">
        <h1>组件库</h1>
        <p>{PANEL_CATALOG.length} 个组件 · {PANEL_GROUPS.length} 个分组 · {withSample} 个带示例</p>
        <span className="spacer" />
        <button type="button" className="density-toggle" aria-pressed={dense} onClick={() => setDense((value) => !value)}>
          {dense ? '显示示例' : '只看说明'}
        </button>
      </div>

      <div className="workspace-body atlas-body">
        <aside className="atlas-nav" aria-label="组件分组">
          <div className="atlas-search">
            <input type="search" placeholder="搜索组件、类型或用途" aria-label="搜索组件" value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>
          <button type="button" className="atlas-nav-item" aria-current={group === '全部' ? 'true' : undefined} onClick={() => jump('全部')}>
            <span>全部</span>
            <em>{PANEL_CATALOG.length}</em>
          </button>
          {PANEL_GROUPS.map((name) => {
            const count = PANEL_CATALOG.filter((meta) => meta.group === name).length;
            return (
              <button key={name} type="button" className="atlas-nav-item" aria-current={group === name ? 'true' : undefined} onClick={() => jump(name)}>
                <span>{name}</span>
                <em>{count}</em>
              </button>
            );
          })}
          <p className="atlas-nav-note">分组只改变这里的浏览，不改变实验装配。</p>
        </aside>

        <div className="atlas-sections">
          {!entries.length ? (
            <StateBlock kind="empty" title="没有匹配的组件" tight>换一个关键词或分组。</StateBlock>
          ) : null}

          {visibleGroups.map((name) => {
            const items = entries.filter((meta) => meta.group === name);
            return (
              <section
                className="atlas-section"
                key={name}
                ref={(node) => { sectionsRef.current[name] = node; }}
              >
                <header className="atlas-section-head">
                  <h2>{name}</h2>
                  <span>{items.length} 个组件</span>
                </header>
                <div className={`atlas-cards${dense ? ' dense' : ''}`}>
                  {items.map((meta) => {
                    const panel = panelByType.get(meta.type);
                    const open = expanded.has(meta.type);
                    return (
                      <article className={`atlas-card${panel ? '' : ' missing'}`} key={meta.type}>
                        <header className="atlas-card-head">
                          <div>
                            <h3>{meta.label}</h3>
                            <code>{meta.type}</code>
                          </div>
                          <span className="atlas-batch">{meta.batch}</span>
                        </header>
                        <p className="atlas-purpose">{meta.purpose}</p>
                        {panel && !dense ? (
                          <>
                            <div className={`atlas-sample${open ? ' open' : ''}`}>
                              {renderPanel(panel, data.datasets, selection, select)}
                            </div>
                            <div className="atlas-card-foot">
                              <span className="atlas-sample-note">示例：{panel.title}</span>
                              <button type="button" className="link-button" onClick={() => toggle(meta.type)}>
                                {open ? '收起' : '展开完整'}
                              </button>
                            </div>
                          </>
                        ) : (
                          <p className="atlas-card-foot muted">{panel ? '说明模式：示例已隐藏' : '尚无示例资料'}</p>
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </>
  );
}
