import React, { useMemo } from 'react';

/**
 * 世界谱系图：按 Archify dataflow 的节点与连线路由思路手写 SVG。
 * 世界为圆角矩形，检查点/来源为小节点，连线用折线并带标签。
 */

const COLORS = {
  base: { fill: '#ffffff', stroke: '#b9cfdd', text: '#1f3a4d' },
  active: { fill: '#f2f8fc', stroke: '#4a8fc2', text: '#274861' },
  awaiting: { fill: '#fbfbfc', stroke: '#dbe3ea', text: '#7b93a6' },
  checkpoint: { fill: '#f5f9fc', stroke: '#a9c4d6', text: '#365b8f' },
};

function layout(worlds) {
  const byId = new Map(worlds.map((world) => [world.id, world]));
  const depth = new Map();
  const resolve = (world) => {
    if (depth.has(world.id)) return depth.get(world.id);
    let value = 0;
    if (world.parentWorldId && byId.has(world.parentWorldId)) value = resolve(byId.get(world.parentWorldId)) + 1;
    depth.set(world.id, value);
    return value;
  };
  worlds.forEach(resolve);

  const columns = new Map();
  for (const world of worlds) {
    const level = depth.get(world.id) || 0;
    if (!columns.has(level)) columns.set(level, []);
    columns.get(level).push(world);
  }

  const nodeW = 208;
  const nodeH = 68;
  const gapX = 74;
  const gapY = 26;
  const padX = 26;
  const padY = 34;
  const levels = [...columns.keys()].sort((a, b) => a - b);
  const maxRows = Math.max(...levels.map((level) => columns.get(level).length), 1);
  const width = padX * 2 + levels.length * nodeW + (levels.length - 1) * gapX;
  const height = padY * 2 + maxRows * nodeH + (maxRows - 1) * gapY;

  const nodes = new Map();
  for (const level of levels) {
    columns.get(level).forEach((world, index) => {
      const columnHeight = columns.get(level).length * nodeH + (columns.get(level).length - 1) * gapY;
      const offset = (height - padY * 2 - columnHeight) / 2;
      nodes.set(world.id, {
        world,
        x: padX + level * (nodeW + gapX),
        y: padY + offset + index * (nodeH + gapY),
        w: nodeW,
        h: nodeH,
      });
    });
  }
  return { nodes, width: Math.max(560, width), height: Math.max(240, height), depth };
}

function route(from, to) {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x - 4;
  const y2 = to.y + to.h / 2;
  const mid = x1 + (x2 - x1) * 0.45;
  return `M ${x1} ${y1} L ${mid} ${y1} L ${mid} ${y2} L ${x2} ${y2}`;
}

export function WorldDiagram({ worlds, runs, onSelectRun, selectedRunId }) {
  const { nodes, width, height } = useMemo(() => layout(worlds), [worlds]);
  const runsByWorld = useMemo(() => {
    const map = new Map();
    for (const run of runs) {
      if (!map.has(run.worldId)) map.set(run.worldId, []);
      map.get(run.worldId).push(run);
    }
    return map;
  }, [runs]);

  return (
    <div className="world-diagram">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="世界来源与运行" style={{ maxWidth: Math.min(width, 780) }}>
        <defs>
          <marker id="world-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 8 4 L 0 8 z" fill="#a9c4d6" />
          </marker>
        </defs>

        {[...nodes.values()].map((node) => {
          const parent = nodes.get(node.world.parentWorldId);
          if (!parent) return null;
          return (
            <g key={`e-${node.world.id}`}>
              <path d={route(parent, node)} fill="none" stroke="#b9cfdd" strokeWidth="1.3" markerEnd="url(#world-arrow)" />
              <text x={parent.x + parent.w + (node.x - parent.x - parent.w) * 0.45 + 5} y={(parent.y + parent.h / 2 + node.y + node.h / 2) / 2} className="world-edge-label">
                分叉
              </text>
            </g>
          );
        })}

        {[...nodes.values()].map(({ world, x, y, w, h }) => {
          const worldRuns = runsByWorld.get(world.id) || [];
          const state = world.availability === 'awaiting' ? 'awaiting' : worldRuns.length ? 'base' : 'awaiting';
          const palette = COLORS[state];
          return (
            <g key={world.id} transform={`translate(${x},${y})`} className="world-node">
              <rect width={w} height={h} rx="9" fill={palette.fill} stroke={palette.stroke} strokeWidth="1.2" />
              <text x="12" y="21" className="world-title">{world.label}</text>
              <text x="12" y="38" className="world-meta">
                {world.parentWorldId ? `来源 ${world.parentWorldId}` : '独立世界'}
              </text>
              <text x="12" y="53" className="world-meta">
                {world.plannedSource ? `计划来源 ${world.plannedSource}` : '来源待补'}
              </text>
              {Object.entries(world.overrides || {}).slice(0, 1).map(([key, value]) => (
                <text key={key} x={w - 12} y="21" textAnchor="end" className="world-param">{key}={String(value)}</text>
              ))}
              {worldRuns.map((run, index) => {
                const cx = w - 12 - index * 10;
                return (
                  <circle
                    key={run.id}
                    cx={cx}
                    cy={h - 12}
                    r="4.5"
                    className={`world-run-dot ${run.status || 'unknown'}${selectedRunId === run.id ? ' active' : ''}`}
                    onClick={() => onSelectRun?.(run.id)}
                    role="button"
                    tabIndex={0}
                    aria-label={`${run.label}，${run.status || '状态待提供'}`}
                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelectRun?.(run.id); }}
                  />
                );
              })}
              {worldRuns.length ? (
                <text x={w - 12 - worldRuns.length * 10} y={h - 8} textAnchor="end" className="world-run-count">
                  {worldRuns.length} 次运行
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <ul className="world-legend">
        <li><i style={{ background: '#eef5fa', border: '1px solid #4a8fc2', borderRadius: 2 }} />有运行的世界</li>
        <li><i style={{ background: '#fbfbfc', border: '1px solid #cfd9e2', borderRadius: 2 }} />尚无运行</li>
        <li><i style={{ background: '#4a8fc2', border: 0, borderRadius: '50%' }} />运行节点（点击查看）</li>
      </ul>
    </div>
  );
}
