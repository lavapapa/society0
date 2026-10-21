import React from 'react';

export function StateBlock({ kind = 'unconfigured', icon, title, children, actions, tight = false }) {
  return (
    <div className={`state-block ${kind}${tight ? ' tight' : ''}`}>
      {icon ? <span className="state-icon">{icon}</span> : null}
      {title ? <h3>{title}</h3> : null}
      {children ? <p>{children}</p> : null}
      {actions ? <div className="state-actions">{actions}</div> : null}
    </div>
  );
}

export function Skeleton({ lines = 3 }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <div key={index} className={`skeleton-line ${index % 3 === 1 ? 'w80' : index % 3 === 2 ? 'w60' : ''}`} />
      ))}
    </div>
  );
}

export function Badge({ state, children }) {
  return <span className={`badge ${state || ''}`}>{children}</span>;
}

export function Notice({ kind, children }) {
  return <div className={`notice${kind ? ` ${kind}` : ''}`}>{children}</div>;
}

export function SectionTitle({ title, note, actions }) {
  return (
    <div className="section-title">
      <h2>{title}</h2>
      {note ? <span>{note}</span> : null}
      {actions ? <span style={{ marginLeft: 'auto' }}>{actions}</span> : null}
    </div>
  );
}

export function Stat({ label, value, note }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </div>
  );
}
