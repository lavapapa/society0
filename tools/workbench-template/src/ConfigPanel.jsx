import React, { useEffect, useState } from "react";
import { getAtPath, pathPart, resolveConfigPath } from "./model";

function Field({ label, path, value, onChange }) {
  if (value && typeof value === "object") {
    return <details className="config-group" open={path.split("/").length <= 3}>
      <summary>{label} <small>{Array.isArray(value) ? `${value.length} 项` : `${Object.keys(value).length} 个字段`}</small></summary>
      <div>{Object.entries(value).map(([key, child]) =>
        <Field key={key} label={key} path={`${path}/${pathPart(key)}`} value={child} onChange={onChange} />
      )}</div>
    </details>;
  }
  return <label className="config-field">
    <span>{label}</span>
    {typeof value === "boolean" ? <select value={String(value)} onChange={(event) => onChange(path, event.target.value === "true")}>
      <option value="true">true</option><option value="false">false</option>
    </select> : typeof value === "number" ? <input type="number" value={value} onChange={(event) => {
      if (event.target.value !== "" && Number.isFinite(Number(event.target.value))) onChange(path, Number(event.target.value));
    }} /> : typeof value === "string" ? <textarea value={value} rows={value.length > 90 ? 4 : 2} onChange={(event) => onChange(path, event.target.value)} /> :
      <span className="muted">null；如需修改，请使用下方完整 JSON 编辑框</span>}
    <small>{path || "/"}</small>
  </label>;
}

export function ConfigPanel({ version, entity, config, onChange }) {
  const path = resolveConfigPath(version.config, config, entity?.configPath ?? "");
  const value = path === null ? undefined : getAtPath(config, path);
  const [jsonText, setJsonText] = useState("");
  const [jsonDirty, setJsonDirty] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!jsonDirty) { setJsonText(JSON.stringify(value, null, 2)); setError(""); }
  }, [value, path, jsonDirty]);
  if (value === undefined) return <div className="empty"><h3>该视角的配置已不存在或位置有变化</h3><p>请检查待发送变更；如需继续调整结构，可在“实验整体”视角中编辑完整配置。</p></div>;
  return <section className="config-panel">
    <div className="config-intro">
      <h2>{entity?.name || "完整实验配置"}</h2>
      <p>当前基于 {version?.name || version?.id}。这里的编辑仅暂存在本页面；复制变更并发送给 agent，核对后才会写入新的实验配置版本。</p>
      {version?.configSource && <small>配置来源：{version.configSource}</small>}
    </div>
    <div className="config-fields"><Field label={entity?.name || "完整配置"} path={path} value={value} onChange={onChange} /></div>
    <details className="json-editor">
      <summary>编辑完整 JSON（可增删字段或调整结构）</summary>
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
