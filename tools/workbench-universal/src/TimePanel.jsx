import React, { useEffect, useMemo, useRef, useState } from 'react';
import { formatTime } from './data.js';
import { useAnchoredPopover } from './useAnchoredPopover.js';

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

function parseDate(value) {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) ? null : date;
}

function iso(date) {
  return date.toISOString().slice(0, 10);
}

function monthMatrix(year, month) {
  const first = new Date(Date.UTC(year, month, 1));
  const offset = (first.getUTCDay() + 6) % 7;
  const start = new Date(first);
  start.setUTCDate(1 - offset);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    return date;
  });
}

/**
 * 时点定位：资料声明用轮次还是日历，界面只呈现那一种形态。
 * 轮次给步进与可用时点列表，日历给月历与同日多点选择。
 */
export function TimePicker({ run, observations, selection, select, mode }) {
  if (!run || !observations.length) return null;
  return <TimeControl observations={observations} selection={selection} select={select} mode={mode} />;
}

export function TimeControl({ observations, selection, select, mode = 'round' }) {
  const hasDates = observations.some((obs) => parseDate(obs.businessTime));
  const calendar = mode === 'calendar' && hasDates;

  const current = observations.find((obs) => obs.id === selection.observationId) || observations[0] || null;
  const index = current ? observations.indexOf(current) : -1;

  const [open, setOpen] = useState(false);
  const trigger = useRef(null);
  const { anchorRef, popoverRef, style: popoverStyle } = useAnchoredPopover(open, 300);

  const anchor = parseDate(current?.businessTime) || parseDate(observations[0]?.businessTime) || new Date();
  const [cursor, setCursor] = useState(() => ({ year: anchor.getUTCFullYear(), month: anchor.getUTCMonth() }));

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const byDate = useMemo(() => {
    const map = new Map();
    for (const obs of observations) {
      const key = obs.businessTime ? String(obs.businessTime).slice(0, 10) : '';
      if (!key) continue;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(obs);
    }
    return map;
  }, [observations]);

  if (!observations.length) return <span className="time-empty">观察时间待提供</span>;

  const step = (delta) => {
    const next = observations[index + delta];
    if (next) select({ observationId: next.id });
  };

  const shiftMonth = (delta) => {
    setCursor((value) => {
      const date = new Date(Date.UTC(value.year, value.month + delta, 1));
      return { year: date.getUTCFullYear(), month: date.getUTCMonth() };
    });
  };

  const selectedKey = current?.businessTime ? String(current.businessTime).slice(0, 10) : '';
  const cells = monthMatrix(cursor.year, cursor.month);

  return (
    <div className="time-control">
      <button type="button" className="icon-button" aria-label="上一个时点" disabled={index <= 0} onClick={() => step(-1)}>‹</button>

      <button
        type="button"
        ref={(node) => { trigger.current = node; anchorRef.current = node; }}
        className="time-current"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        <strong>{current.label}</strong>
        <small>{formatTime(current.businessTime)}</small>
        <span className="caret" aria-hidden="true">▾</span>
      </button>

      <button type="button" className="icon-button" aria-label="下一个时点" disabled={index < 0 || index >= observations.length - 1} onClick={() => step(1)}>›</button>


      {open && !calendar ? (
        <div className="observation-list" role="dialog" aria-label="选择时点" ref={popoverRef} style={popoverStyle}>
          <div className="observation-head">
            <strong>可用时点</strong>
            <small>{observations.length} 个</small>
          </div>
          <ul>
            {observations.map((obs) => (
              <li key={obs.id}>
                <button
                  type="button"
                  aria-pressed={obs.id === current.id}
                  onClick={() => { select({ observationId: obs.id }); setOpen(false); }}
                >
                  <span>{obs.label}</span>
                  <small>{formatTime(obs.businessTime)}</small>
                  {obs.availability === 'partial' ? <em>部分资料</em> : null}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {open && calendar ? (
        <div className="calendar" role="dialog" aria-label="按日期选择观测点" ref={popoverRef} style={popoverStyle}>
          <div className="calendar-head">
            <button type="button" className="icon-button" aria-label="上个月" onClick={() => shiftMonth(-1)}>‹</button>
            <strong>{cursor.year} 年 {cursor.month + 1} 月</strong>
            <button type="button" className="icon-button" aria-label="下个月" onClick={() => shiftMonth(1)}>›</button>
          </div>
          <div className="calendar-grid">
            {WEEKDAYS.map((day) => <span key={day} className="calendar-weekday">{day}</span>)}
            {cells.map((date) => {
              const key = iso(date);
              const items = byDate.get(key) || [];
              const outside = date.getUTCMonth() !== cursor.month;
              const active = key === selectedKey;
              return (
                <button
                  key={key}
                  type="button"
                  className={`calendar-day${outside ? ' outside' : ''}${active ? ' active' : ''}${items.length ? ' has-data' : ''}`}
                  aria-label={`${key}${items.length ? `，${items.length} 个观测点` : '，无资料'}`}
                  onClick={() => {
                    if (!items.length) return;
                    select({ observationId: items[0].id });
                    if (items.length === 1) setOpen(false);
                  }}
                >
                  <span>{date.getUTCDate()}</span>
                  {items.length ? <i className="day-dot" aria-hidden="true" /> : null}
                </button>
              );
            })}
          </div>
          {byDate.get(selectedKey)?.length > 1 ? (
            <div className="calendar-selection">
              <small>同日 {byDate.get(selectedKey).length} 个观测点</small>
              <div className="same-day">
                {byDate.get(selectedKey).map((obs) => (
                  <button key={obs.id} type="button" aria-pressed={obs.id === current.id} onClick={() => { select({ observationId: obs.id }); setOpen(false); }}>{obs.label}</button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
