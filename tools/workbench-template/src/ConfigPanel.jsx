import React, { useEffect, useState } from "react";
import { getAtPath, pathPart, resolveConfigPath } from "./model";

export function Field({ label, path, value, onChange, fields }) {
  const info = fields[path] || fields[path.replace(/\/\d+(?=\/|$)/g, '/*')] || {};
  const title = info.label || label;
  if (value && typeof value === "object") {
    return <details className="config-group" open={path.split("/").length <= 3}>
      <summary>{title} <small>{Array.isArray(value) ? `${value.length} 项` : `${Object.keys(value).length} 个字段`}</small></summary>
      {info.description && <p className="config-group-description">{info.description}</p>}
      <div>{Object.entries(value).map(([key, child]) =>
        <Field key={key} label={key} path={`${path}/${pathPart(key)}`} value={child} onChange={onChange} fields={fields} />
      )}</div>
    </details>;
  }
  return <label className="config-field">
    <span className="config-field-label" title={path || "/"}><b>{title}</b><code>{label}</code><small>{info.description || '此字段的含义待补充，请向创建工作台的 agent 确认。'}</small>{info.effect && <small className="config-effect">调整影响：{info.effect}</small>}</span>
    {typeof value === "boolean" ? <select aria-label={title} value={String(value)} onChange={(event) => onChange(path, event.target.value === "true")}>
      <option value="true">是</option><option value="false">否</option>
    </select> : typeof value === "number" ? <input aria-label={title} type="number" value={value} onChange={(event) => {
      if (event.target.value !== "" && Number.isFinite(Number(event.target.value))) onChange(path, Number(event.target.value));
    }} /> : typeof value === "string" ? <textarea aria-label={title} value={value} rows={Math.min(8, value.split('\n').reduce((rows, line) => rows + Math.max(1, Math.ceil(line.length / 26)), 0))} onChange={(event) => onChange(path, event.target.value)} /> :
      <span className="muted">null；如需修改，请使用下方完整 JSON 编辑框</span>}
  </label>;
}

export function ConfigPanel({ version, entity, config, onChange }) {
  const path = resolveConfigPath(version.config, config, entity?.configPath ?? "");
  const value = path === null ? undefined : getAtPath(config, path);
  const visibleValue = entity?.configExclude && value && typeof value === 'object'
    ? Object.fromEntries(Object.entries(value).filter(([key])=>!entity.configExclude.includes(key))) : value;
  const [jsonText, setJsonText] = useState("");
  const [jsonDirty, setJsonDirty] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!jsonDirty) { setJsonText(JSON.stringify(value, null, 2)); setError(""); }
  }, [value, path, jsonDirty]);
  if (value === undefined) return <div className="empty"><h3>该视角的配置已不存在或位置有变化</h3><p>请检查待发送变更；如需继续调整结构，可在“实验整体”视角中编辑完整配置。</p></div>;
  return <section className="config-panel">
    <div className="config-intro">
      <p>编辑后，点击顶部“复制变更”发给 agent，核对并创建新版本。</p>
      {version?.configSource && <details><summary>配置来源 · {version?.name || version?.id}</summary><small>{version.configSource}</small></details>}
    </div>
    <div className="config-fields"><Field label={entity?.name || "完整配置"} path={path} value={visibleValue} onChange={onChange} fields={version.configFields || {}} /></div>
    <details className="json-editor">
      <summary>编辑当前视角 JSON（可增删字段或调整结构）</summary>
      <textarea aria-label="完整配置 JSON" value={jsonText} onChange={(event) => { setJsonText(event.target.value); setJsonDirty(true); }} rows={16} spellCheck={false} />
      {jsonDirty && <p className="config-unsaved">这段 JSON 尚未暂存。请点击下方按钮，再复制变更。</p>}
      {error && <p role="alert" className="config-error">{error}</p>}
      <button onClick={() => {
        try { onChange(path, JSON.parse(jsonText)); setJsonDirty(false); setError(""); }
        catch (problem) { setError(`JSON 格式有误：${problem.message}`); }
      }}>暂存 JSON 修改</button>
    </details>
  </section>;
}
