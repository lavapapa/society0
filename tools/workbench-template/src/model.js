// 数据以配置版本为单位；每次试运行都归属于生成它的版本。
export const blankStudy = {
  study: { title: "Society0 工作台", question: "" },
  versions: [],
};

export function snapshotFor(data, versionId, runId, tickId, entityId) {
  const version = data.versions?.find((item) => item.id === versionId);
  const run = version?.runs?.find((item) => item.id === runId);
  return run?.snapshots?.find((item) => item.tickId === tickId && item.entityId === entityId) || null;
}

export function mainTabsFor(snapshot, entity, hasConfig = false) {
  const tabs = [];
  if (snapshot && (entity?.kind === "llm-agent" || snapshot.mechanisms?.length)) {
    tabs.push({ id: "mechanisms", title: "可接触的信息" });
  }
  if (hasConfig) tabs.push({ id: "config", title: "配置检查" });
  return tabs.concat(snapshot?.tabs || []);
}

export function rightTabsFor(snapshot, entity, hasConfig = false) {
  const tabs = [];
  if (snapshot && entity?.kind === "llm-agent") tabs.push({ id: "sessions", title: "Agent 会话" });
  if (hasConfig) tabs.push({ id: "changes", title: "待发送变更" });
  return tabs.concat(snapshot?.sideTabs || []);
}

const parts = (path) => path === "" ? [] : path.slice(1).split("/").map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
export const pathPart = (part) => String(part).replace(/~/g, "~0").replace(/\//g, "~1");

export function getAtPath(value, path) {
  return parts(path).reduce((current, part) => current?.[part], value);
}

export function resolveConfigPath(original, draft, path) {
  if (!path) return "";
  let before = original;
  let after = draft;
  const resolved = [];
  for (const part of parts(path)) {
    let nextPart = part;
    if (Array.isArray(before) && Array.isArray(after)) {
      const item = before[part];
      if (item && typeof item === "object" && item.id !== undefined) {
        nextPart = after.findIndex((candidate) => candidate?.id === item.id);
        if (nextPart < 0) return null;
      } else if (before.length !== after.length) return null;
    }
    before = before?.[part];
    after = after?.[nextPart];
    if (after === undefined) return null;
    resolved.push(pathPart(nextPart));
  }
  return `/${resolved.join("/")}`;
}

export function setAtPath(value, path, next) {
  if (path === "") return next;
  const route = parts(path);
  const copy = structuredClone(value);
  let node = copy;
  for (const part of route.slice(0, -1)) node = node[part];
  node[route.at(-1)] = next;
  return copy;
}

export function changesBetween(before, after, path = "") {
  if (Object.is(before, after)) return [];
  const plain = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  if (plain(before) && plain(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    return [...keys].flatMap((key) => changesBetween(before[key], after[key], `${path}/${pathPart(key)}`));
  }
  if (Array.isArray(before) && Array.isArray(after) && before.length === after.length) {
    return before.flatMap((value, index) => changesBetween(value, after[index], `${path}/${index}`));
  }
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (before === undefined) return [{ op: "add", path, after }];
  if (after === undefined) return [{ op: "remove", path, before }];
  return [{ op: "replace", path, before, after }];
}

export function changeRequest(version, changes) {
  return {
    type: "society0_config_change_request",
    baseVersionId: version.id,
    baseVersionName: version.name || version.id,
    configSource: version.configSource || "",
    instruction: "请核对这些拟议修改，在实验文件中创建新的配置版本并更新工作台；页面本身未修改实验文件，也未启动试运行。",
    changes,
  };
}

export function visibleRows(count, scrollTop, viewportHeight, rowHeight = 40, overscan = 3) {
  const capacity = Math.ceil(viewportHeight / rowHeight);
  const start = Math.min(Math.max(0, Math.floor(scrollTop / rowHeight) - overscan), Math.max(0, count - capacity));
  const end = Math.min(count, Math.ceil((scrollTop + viewportHeight) / rowHeight) + overscan);
  return { start, end };
}
