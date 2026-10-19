import {getAtPath, setAtPath, resolveConfigPath} from './model.js';

export function pluginSettingsTarget(version, plugin) {
  if (!plugin.configPath) return null;
  return (version.entities || []).filter(entity => entity.kind === 'overview' && entity.configPath &&
    (plugin.configPath === entity.configPath || plugin.configPath.startsWith(`${entity.configPath}/`)))
    .sort((a,b) => b.configPath.length - a.configPath.length)[0] || null;
}

export function pluginsFor(version, config) {
  return (version?.plugins || []).map(plugin => {
    const view = version.pluginViews?.[plugin.name] || {};
    const locked = view.category === 'builtin' || view.fixed === true;
    const enabledPath = view.enabledPath ? resolveConfigPath(version.config, config, view.enabledPath) : null;
    const configPath = view.configPath ? resolveConfigPath(version.config, config, view.configPath) : null;
    const blockedReason = !locked && (view.requiresEnabledPaths || []).some(path => {
      const resolved = resolveConfigPath(version.config, config, path);
      return resolved === null || getAtPath(config, resolved) !== true;
    })
      ? '依赖的机制已停用；重新启用后恢复此处的选择。' : '';
    const problem = !['builtin','social'].includes(view.category) ? '请补充插件分类与说明。'
      : !locked && (enabledPath === null || typeof getAtPath(config, enabledPath) !== 'boolean') ? '请绑定实验配置中的布尔启停字段。'
      : view.configPath && configPath === null ? '参数路径已不存在，请核对配置与字段绑定。' : '';
    return {...plugin, ...view, title:view.title || plugin.name, locked, enabledPath, configPath, blockedReason,
      enabled:view.category === 'builtin' || (view.fixed ? view.enabled !== false : !blockedReason && enabledPath !== null && getAtPath(config, enabledPath) === true), problem};
  });
}

export function pluginToggle(version, config, name) {
  const plugin = pluginsFor(version, config).find(item => item.name === name);
  if (plugin.locked) throw new Error(plugin.fixed?'历史固定装配，请先创建可编辑版本。':'内置插件保持启用。');
  if (plugin.problem) throw new Error(plugin.problem);
  if (plugin.blockedReason) throw new Error(plugin.blockedReason);
  return setAtPath(config, plugin.enabledPath, !plugin.enabled);
}
