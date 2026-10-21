import React, { useState } from 'react';
import { PANEL_CATALOG, PANEL_GROUPS } from './catalog.js';
import { PanelGrid, renderPanel } from './Panels.jsx';
import { StateBlock } from './ui.jsx';

/** 组件图鉴：按分组列出可用组件，并直接渲染同一份 mock 资料。 */
export function AtlasMain({ data, selection, select, layout, onLayout }) {
  const [group, setGroup] = useState('全部');
  const panels = data.panels.filter((panel) => {
    if (group === '全部') return true;
    const meta = PANEL_CATALOG.find((item) => item.type === panel.type);
    return meta?.group === group;
  });
  const groups = ['全部', ...PANEL_GROUPS];
  const shownTypes = new Set(panels.map((panel) => panel.type));

  return (
    <>
      <div className="workspace-head">
        <h1>组件图鉴</h1>
        <p>{PANEL_CATALOG.length} 个组件 · {panels.length} 个示例</p>
      </div>
      <div className="workspace-body">
        <p className="lede">同一份 mock 资料下的组件示例。分组筛选只改变图鉴视图，不改变你的实验装配。</p>
        <div className="atlas-groups" role="group" aria-label="组件分组">
          {groups.map((item) => (
            <button key={item} type="button" aria-pressed={group === item} onClick={() => setGroup(item)}>
              {item}
              <span>{item === '全部' ? PANEL_CATALOG.length : PANEL_CATALOG.filter((meta) => meta.group === item).length}</span>
            </button>
          ))}
        </div>
        <ul className="atlas-index">
          {PANEL_CATALOG.filter((meta) => group === '全部' || meta.group === group).map((meta) => (
            <li key={meta.type} className={shownTypes.has(meta.type) ? 'ready' : 'missing'}>
              <code>{meta.type}</code>
              <strong>{meta.label}</strong>
              <span>{meta.purpose}</span>
              <em>{meta.batch}</em>
            </li>
          ))}
        </ul>
        <div className="atlas-grid">
          <PanelGrid panels={panels} layout={layout} onLayout={onLayout} renderPanel={(panel) => renderPanel(panel, data.datasets, selection, select)} />
        </div>
        {!panels.length ? <StateBlock kind="empty" title="这个分组还没有示例" tight>换一个分组。</StateBlock> : null}
      </div>
    </>
  );
}
