import React, { useMemo, useState } from 'react';
import { formatTime } from './data.js';
import { Badge, StateBlock } from './ui.jsx';

/* ---------- 事件日历（月视图 + 事件层） ---------- */

const WEEK = ['一', '二', '三', '四', '五', '六', '日'];

function parseDay(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!match) return null;
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
}

function dayKey(date) {
  return date.toISOString().slice(0, 10);
}

export function EventCalendarPanel({ dataset }) {
  const events = dataset?.events || [];
  const layers = useMemo(() => [...new Set(events.map((event) => event.layer || '事件'))], [events]);
  const [activeLayers, setActiveLayers] = useState(() => new Set(layers));
  const anchor = events.map((event) => parseDay(event.start)).filter(Boolean)[0] || new Date();
  const [cursor, setCursor] = useState({ year: anchor.getUTCFullYear(), month: anchor.getUTCMonth() });

  if (!events.length) return <StateBlock kind="empty" title="没有事件资料" tight>公告、生效、到期与主体激活会出现在这里。</StateBlock>;

  const byDay = new Map();
  for (const event of events) {
    if (!activeLayers.has(event.layer || '事件')) continue;
    const key = String(event.start || '').slice(0, 10);
    if (!key) continue;
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(event);
  }

  const first = new Date(Date.UTC(cursor.year, cursor.month, 1));
  const offset = (first.getUTCDay() + 6) % 7;
  const start = new Date(first);
  start.setUTCDate(1 - offset);
  const cells = Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    return date;
  });

  const shift = (delta) => {
    const next = new Date(Date.UTC(cursor.year, cursor.month + delta, 1));
    setCursor({ year: next.getUTCFullYear(), month: next.getUTCMonth() });
  };

  const toggle = (layer) => {
    setActiveLayers((current) => {
      const next = new Set(current);
      if (next.has(layer)) next.delete(layer); else next.add(layer);
      return next;
    });
  };

  const monthEvents = [...byDay.entries()]
    .filter(([key]) => key.startsWith(`${cursor.year}-${String(cursor.month + 1).padStart(2, '0')}`))
    .flatMap(([, items]) => items);

  return (
    <div className="event-calendar">
      <div className="calendar-controls">
        <button type="button" className="icon-button" aria-label="上个月" onClick={() => shift(-1)}>‹</button>
        <strong>{cursor.year} 年 {cursor.month + 1} 月</strong>
        <button type="button" className="icon-button" aria-label="下个月" onClick={() => shift(1)}>›</button>
        <span className="spacer" style={{ flex: 1 }} />
        {layers.map((layer) => (
          <button key={layer} type="button" className="layer-toggle" aria-pressed={activeLayers.has(layer)} onClick={() => toggle(layer)}>{layer}</button>
        ))}
      </div>
      <div className="month-grid">
        {WEEK.map((day) => <span key={day} className="month-weekday">{day}</span>)}
        {cells.map((date) => {
          const key = dayKey(date);
          const items = byDay.get(key) || [];
          const outside = date.getUTCMonth() !== cursor.month;
          return (
            <div key={key} className={`month-cell${outside ? ' outside' : ''}`}>
              <span className="month-date">{date.getUTCDate()}</span>
              {items.slice(0, 3).map((event) => (
                <span
                  key={event.id}
                  className={`event-chip layer-${layers.indexOf(event.layer || '事件') % 4}`}
                  tabIndex={0}
                  aria-label={`${event.title}，${event.start}${event.until ? ` 至 ${event.until}` : ''}`}
                  title={`${event.title}${event.until ? ` · 至 ${event.until}` : ''}`}
                >
                  {event.short || event.title}
                </span>
              ))}
              {items.length > 3 ? <span className="event-more">+{items.length - 3}</span> : null}
            </div>
          );
        })}
      </div>
      {monthEvents.length ? (
        <ul className="month-event-list">
          {monthEvents.map((event) => (
            <li key={`list-${event.id}`}>
              <span className="event-date">{String(event.start).slice(5)}</span>
              <span className="event-title">{event.title}</span>
              {event.until ? <small>至 {String(event.until).slice(5)}</small> : null}
              <em>{event.layer || '事件'}</em>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="panel-source">本月 {monthEvents.length} 个事件；区间事件在开始日记入，结束日期在右侧列出。{dataset.source ? `来源：${dataset.source}` : ''}</p>
    </div>
  );
}

/* ---------- 时间线 / 泳道 ---------- */

export function TimelinePanel({ dataset }) {
  const lanes = dataset?.lanes || [];
  if (!lanes.length) return <StateBlock kind="empty" title="没有时间线资料" tight>需要至少一个泳道。</StateBlock>;
  const start = dataset.start || lanes.flatMap((lane) => lane.items || []).map((item) => item.at).sort()[0];
  const end = dataset.end || lanes.flatMap((lane) => lane.items || []).map((item) => item.until || item.at).sort().at(-1);
  const min = parseDay(start)?.getTime() ?? 0;
  const max = parseDay(end)?.getTime() ?? min + 1;
  const span = Math.max(1, max - min);

  return (
    <div className="timeline">
      {lanes.map((lane) => (
        <div className="timeline-lane" key={lane.id || lane.label}>
          <span className="lane-label">{lane.label}</span>
          <div className="lane-track">
            {(lane.items || []).map((item) => {
              const at = parseDay(item.at);
              const until = parseDay(item.until);
              if (!at) return null;
              const left = ((at.getTime() - min) / span) * 100;
              const width = until ? Math.max(1.2, ((until.getTime() - at.getTime()) / span) * 100) : 0;
              return item.until ? (
                <span key={item.id} className="lane-range" style={{ left: `${left}%`, width: `${width}%` }} title={`${item.title} · ${item.at} ~ ${item.until}`}>{item.title}</span>
              ) : (
                <span key={item.id} className="lane-point" style={{ left: `${left}%` }} title={`${item.title} · ${item.at}`} />
              );
            })}
          </div>
        </div>
      ))}
      <div className="timeline-axis"><span>{formatTime(start)}</span><span>{formatTime(end)}</span></div>
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 甘特 ---------- */

export function GanttPanel({ dataset }) {
  const tasks = dataset?.tasks || [];
  if (!tasks.length) return <StateBlock kind="empty" title="没有任务区间" tight>计划与实际区间随资料进入。</StateBlock>;
  const all = tasks.flatMap((task) => [task.planned, task.actual].filter(Boolean));
  const min = Math.min(...all.map((range) => parseDay(range.start)?.getTime() ?? 0));
  const max = Math.max(...all.map((range) => parseDay(range.end)?.getTime() ?? 0));
  const span = Math.max(1, max - min);
  const pos = (date) => ((parseDay(date)?.getTime() - min) / span) * 100;

  return (
    <div className="gantt">
      {tasks.map((task) => {
        const status = task.status || 'unknown';
        return (
          <div className="gantt-row" key={task.id}>
            <span className="gantt-label">{task.label}</span>
            <div className="gantt-track">
              {task.planned ? (
                <span className="gantt-bar planned" style={{ left: `${pos(task.planned.start)}%`, width: `${Math.max(1, pos(task.planned.end) - pos(task.planned.start))}%` }} title={`计划 ${task.planned.start} ~ ${task.planned.end}`} />
              ) : null}
              {task.actual ? (
                <span className={`gantt-bar actual ${status}`} style={{ left: `${pos(task.actual.start)}%`, width: `${Math.max(1, pos(task.actual.end) - pos(task.actual.start))}%` }} title={`实际 ${task.actual.start} ~ ${task.actual.end}`} />
              ) : null}
              {status === 'running' && task.actual ? <span className="gantt-now" style={{ left: `${pos(task.actual.end)}%` }} /> : null}
            </div>
            <span className="gantt-status">
              <Badge state={status === 'failed' ? 'error' : status === 'completed' ? 'ready' : undefined}>{statusLabel(status)}</Badge>
            </span>
          </div>
        );
      })}
      <p className="panel-source">细条为计划，实条为实际。未提供结束时间不画完成条。{dataset.source ? `来源：${dataset.source}` : ''}</p>
    </div>
  );
}

function statusLabel(status) {
  return { queued: '等待', running: '进行中', completed: '已完成', failed: '失败', paused: '暂停', unknown: '状态待提供' }[status] || '状态待提供';
}

/* ---------- 事项板 ---------- */

export function BoardPanel({ dataset }) {
  const columns = dataset?.columns || [];
  if (!columns.length) return <StateBlock kind="empty" title="没有事项资料" tight>事项与状态随资料进入。</StateBlock>;
  return (
    <div className="board">
      {columns.map((column) => (
        <section className="board-column" key={column.id} aria-label={column.label}>
          <header><strong>{column.label}</strong><span>{column.items?.length || 0}</span></header>
          <ul>
            {(column.items || []).map((item) => (
              <li key={item.id} className="board-card">
                <strong>{item.title}</strong>
                <small>{item.actor || '未指派'}{item.due ? ` · 到期 ${formatTime(item.due)}` : ''}</small>
                {item.note ? <p>{item.note}</p> : null}
              </li>
            ))}
            {!column.items?.length ? <li className="board-empty">暂无事项</li> : null}
          </ul>
        </section>
      ))}
      {dataset.source ? <p className="panel-source">来源：{dataset.source}</p> : null}
    </div>
  );
}

/* ---------- 流程状态 ---------- */

export function FlowPanel({ dataset }) {
  const steps = dataset?.steps || [];
  if (!steps.length) return <StateBlock kind="empty" title="没有流程资料" tight>阶段与状态随资料进入。</StateBlock>;
  return (
    <ol className="flow">
      {steps.map((step) => (
        <li key={step.id} className={`flow-step ${step.state || 'pending'}`}>
          <span className="flow-dot" aria-hidden="true" />
          <div>
            <strong>{step.label}</strong>
            {step.note ? <small>{step.note}</small> : null}
          </div>
          <span className="flow-time">{step.at ? formatTime(step.at) : '—'}</span>
        </li>
      ))}
    </ol>
  );
}
