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
    documents: list(data.documents),
    artifacts: list(data.artifacts),
    workspace: {
      pages: list(data.workspace?.pages),
      panels: list(data.workspace?.panels),
      defaultSelection: data.workspace?.defaultSelection || {},
      timeModes: list(data.workspace?.timeModes),
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

/** 配置分区固定顺序：插件配置、平行世界必须位于最前两项。 */
export function configSections(data, version) {
  const custom = list(version?.configSections).length ? version.configSections : data.workspace.configSections;
  const rest = list(custom).filter((section) => section.id !== 'plugins' && section.id !== 'worlds');
  return [
    { id: 'plugins', label: '插件配置' },
    { id: 'worlds', label: '平行世界' },
    ...rest.map((section) => ({ id: section.id, label: section.label })),
  ];
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
