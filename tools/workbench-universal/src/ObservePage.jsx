import React, { useMemo, useState } from 'react';
import { PANEL_TYPES, PanelGrid, renderPanel } from './Panels.jsx';

/** 观察页：按 panel 声明装配网格；无面板时给出明确空态。 */
export function ObserveMain({ data, selection, select, layout, onLayout }) {
  const panels = data.panels;
  const datasets = data.datasets;

  return (
    <>
      <div className="workspace-head">
        <h1>{data.study.title}</h1>
        <p>{data.study.question || '观察结果随资料进入'}</p>
      </div>
      <div className="workspace-body">
        {data.study.description ? <p className="lede">{data.study.description}</p> : null}
        <PanelGrid
          panels={panels}
          layout={layout}
          onLayout={onLayout}
          renderPanel={(panel) => renderPanel(panel, datasets, selection, select)}
        />
      </div>
    </>
  );
}

const EMPTY_LAYOUT = { order: [], sizes: {} };

export function useLayout(storageKey) {
  const [layout, setLayout] = useState(() => {
    try {
      const text = window.localStorage.getItem(`${storageKey}:panels`);
      return text ? JSON.parse(text) : EMPTY_LAYOUT;
    } catch {
      return EMPTY_LAYOUT;
    }
  });
  const update = (next) => {
    setLayout((current) => {
      const resolved = typeof next === 'function' ? next(current) : next;
      try {
        window.localStorage.setItem(`${storageKey}:panels`, JSON.stringify(resolved));
      } catch {
        /* 忽略存储不可用 */
      }
      return resolved;
    });
  };
  return [layout, update];
}

export { PANEL_TYPES };
