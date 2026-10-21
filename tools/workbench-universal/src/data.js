const AVAILABILITY = ['unconfigured', 'awaiting', 'loading', 'empty', 'partial', 'ready', 'error', 'stale'];

export const AVAILABILITY_LABELS = {
  unconfigured: '尚未配置',
  awaiting: '待提供资料',
  loading: '正在读取',
  empty: '范围内无记录',
  partial: '部分资料',
  ready: '资料完整',
  error: '读取失败',
  stale: '资料已更新',
};

export const RUN_STATUS_LABELS = {
  queued: '等待执行',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  failed: '运行失败',
  cancelled: '已取消',
  unknown: '状态待提供',
};

export function availabilityOf(node) {
  const value = node && node.availability;
  return AVAILABILITY.includes(value) ? value : 'ready';
}

export function hasContent(node) {
  const state = availabilityOf(node);
  return state === 'ready' || state === 'partial' || state === 'stale';
}

export function list(value) {
  return Array.isArray(value) ? value : [];
}

/** 把外部资料整理成界面可以直接消费的形状；缺失的集合一律为空。 */
export function normalize(raw) {
  const data = raw && typeof raw === 'object' ? raw : {};
  const study = data.study || {};
  return {
    schemaVersion: data.schemaVersion ?? 1,
    dataKind: data.dataKind || 'mock',
    study: {
      id: study.id || 'study-unknown',
      title: study.title || '未命名实验',
      question: study.question || '',
      description: study.description || '',
      dataRevision: study.dataRevision || '',
      updatedAt: study.updatedAt || '',
    },
    versions: list(data.versions),
    plugins: list(data.plugins),
    worlds: list(data.worlds),
    runs: list(data.runs),
    entities: list(data.entities),
    observations: list(data.observations),
    datasets: list(data.datasets),
    panels: list(data.panels || data.workspace?.panels),
    atlas: Boolean(data.atlas),
    documents: list(data.documents),
    artifacts: list(data.artifacts),
    workspace: {
      pages: list(data.workspace?.pages),
      panels: list(data.workspace?.panels),
      defaultSelection: data.workspace?.defaultSelection || {},
      timeMode: data.workspace?.timeMode || 'round',
      activeRunId: data.workspace?.activeRunId || '',
      configSections: list(data.workspace?.configSections),
      entityGroups: list(data.workspace?.entityGroups),
    },
  };
}

export function findVersion(data, id) {
  return data.versions.find((item) => item.id === id) || data.versions[0] || null;
}

export function runsOf(data, versionId) {
  if (!versionId) return data.runs;
  const filtered = data.runs.filter((run) => !run.versionId || run.versionId === versionId);
  return filtered;
}

export function worldsOfVersion(data, versionId) {
  if (!versionId) return data.worlds;
  return data.worlds.filter((world) => !world.versionId || world.versionId === versionId);
}

/**
 * 配置分区：插件配置与平行世界固定在前，其余由已装插件派生。
 * 插件声明 category 与 configSection，两者都参与归组；未声明分区的插件归入「其他插件配置」。
 */
const SECTION_ICONS = {
  plugins: 'grid',
  worlds: 'branch',
  environment: 'clock',
  agents: 'user',
  market: 'chart',
  memory: 'layers',
  runtime: 'cpu',
  other: 'dot',
};

const CATEGORY_SECTION = {
  环境: 'environment',
  时间: 'environment',
  主体: 'agents',
  决策: 'agents',
  市场: 'market',
  记忆: 'memory',
  运行: 'runtime',
};

const SECTION_LABELS = {
  environment: '环境与时间',
  agents: '主体与决策',
  market: '市场与价格',
  memory: '记忆与检索',
  runtime: '运行时与调度',
  other: '其他插件配置',
};

export function configSections(data, version) {
  const declared = list(version?.configSections).length ? version.configSections : data.workspace.configSections;
  const dynamic = new Map();
  for (const plugin of data.plugins) {
    const id = plugin.configSection || CATEGORY_SECTION[plugin.category] || 'other';
    if (id === 'plugins' || id === 'worlds') continue;
    if (!dynamic.has(id)) {
      const declaredLabel = list(declared).find((section) => section.id === id)?.label;
      dynamic.set(id, { id, label: declaredLabel || SECTION_LABELS[id] || id, plugins: [] });
    }
    dynamic.get(id).plugins.push(plugin.id);
  }
  return [
    { id: 'plugins', label: '插件配置', icon: SECTION_ICONS.plugins },
    { id: 'worlds', label: '平行世界', icon: SECTION_ICONS.worlds },
    ...[...dynamic.values()].map((section) => ({ ...section, icon: SECTION_ICONS[section.id] || SECTION_ICONS.other })),
  ];
}

/** 当前观察所用的运行：由资料声明，不在界面里切换。 */
export function activeRunOf(data, versionId) {
  const candidates = runsOf(data, versionId);
  const declared = data.workspace.activeRunId;
  return candidates.find((run) => run.id === declared)
    || candidates.find((run) => run.status === 'completed' && run.dataThrough)
    || candidates.find((run) => run.status === 'running')
    || candidates[0]
    || null;
}

export function entityGroups(data, version) {
  const custom = list(version?.entityGroups).length ? version.entityGroups : data.workspace.entityGroups;
  if (list(custom).length) return custom;
  const groups = new Map();
  for (const entity of data.entities) {
    const key = entity.group || '未分组';
    if (!groups.has(key)) groups.set(key, { id: key, label: key, kinds: new Set(), count: 0 });
    const group = groups.get(key);
    group.count += 1;
    if (entity.kind) group.kinds.add(entity.kind);
  }
  return [...groups.values()].map((group) => ({ ...group, kinds: [...group.kinds] }));
}

export function formatTime(value) {
  if (!value) return '时间待提供';
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.replace('T', ' ').replace(/:00(\.\d+)?$/, '').slice(0, text.length > 10 ? 16 : 10);
  return text;
}

export function describeRevision(data) {
  if (!data.study.dataRevision) return '资料版本待提供';
  return `资料 r${data.study.dataRevision}`;
}
