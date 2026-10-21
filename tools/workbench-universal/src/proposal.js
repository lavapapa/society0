/** 配置草稿与复制提议：页面只产生请求文本，不写实验文件。 */

export function pointer(path) {
  return path
    .split('/')
    .slice(1)
    .map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'))
    .join('.');
}

export function valueAt(config, path) {
  if (!path || path === '') return config;
  return path
    .split('/')
    .slice(1)
    .reduce((node, part) => {
      if (node == null) return undefined;
      const key = part.replace(/~1/g, '/').replace(/~0/g, '~');
      return node[key];
    }, config);
}

export function withValueAt(config, path, value) {
  const clone = structuredClone(config ?? {});
  const parts = path.split('/').slice(1).map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'));
  if (!parts.length) return value;
  let node = clone;
  parts.slice(0, -1).forEach((part) => {
    if (node[part] == null || typeof node[part] !== 'object') node[part] = {};
    node = node[part];
  });
  node[parts.at(-1)] = value;
  return clone;
}

function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** 逐字段比较草稿与原始配置，得到可复制的变更列表。 */
export function diffConfig(base, draft, schema = []) {
  const paths = new Set(schema.map((field) => field.path));
  const walk = (node, path) => {
    if (node && typeof node === 'object' && !Array.isArray(node)) {
      Object.entries(node).forEach(([key, value]) => walk(value, `${path}/${key}`));
      return;
    }
    paths.add(path);
  };
  walk(base ?? {}, '');
  walk(draft ?? {}, '');
  const changes = [];
  for (const path of [...paths].sort()) {
    if (!path) continue;
    const before = valueAt(base, path);
    const after = valueAt(draft, path);
    if (same(before, after)) continue;
    if (before === undefined) changes.push({ op: 'add', path, after });
    else if (after === undefined) changes.push({ op: 'remove', path, before });
    else changes.push({ op: 'replace', path, before, after });
  }
  return changes;
}

export function buildProposal({ version, configSource, changes }) {
  return {
    type: 'society0_config_change_request',
    baseVersionId: version?.id ?? '',
    baseVersionName: version?.label ?? '',
    configSource: configSource ?? '',
    changes,
    instruction: '请核对原值后创建新配置版本并更新工作台，暂时不要运行实验。',
  };
}

export function proposalText(proposal) {
  return JSON.stringify(proposal, null, 2);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
