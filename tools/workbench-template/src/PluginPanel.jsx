import React, {useEffect, useState} from 'react';
import {IconLock, IconSearch, IconInfoCircle, IconArrowUpRight} from '@tabler/icons-react';
import {pluginsFor, pluginToggle, pluginSettingsTarget} from './plugins';
import './plugins.css';

const categories = [['builtin','内置'],['social','社交网络扩展']];
export function PluginPanel({version, config, onChange, onOpenSettings}) {
  const [query,setQuery] = useState('');
  const plugins = pluginsFor(version,config);
  useEffect(()=>setQuery(''),[version?.id]);
  const matching = plugins.filter(p=>`${p.title} ${p.name}`.toLowerCase().includes(query.toLowerCase()));
  return <main id="main-content" className="plugin-workspace">
    <div className="plugin-tools"><label className="search"><IconSearch size={14}/><input aria-label="搜索插件" placeholder="搜索插件" value={query} onChange={e=>setQuery(e.target.value)}/></label></div>
    {plugins.some(p=>p.problem)&&<p className="plugin-problem" role="status">{plugins.filter(p=>p.problem).map(p=>`${p.title}：${p.problem}`).join(' ')}</p>}
    {categories.map(([id,label])=><section className="plugin-category" key={id} aria-label={label}>
      <h2>{label}<small>{matching.filter(p=>p.category===id).length}</small></h2>
      <div className="plugin-grid">
        {matching.filter(p=>p.category===id).map(p=>{
          const target=pluginSettingsTarget(version,p);
          return <article className="plugin-card" id={`plugin-${p.name}`} key={p.name}>
            <header><h3>{p.title}</h3><button role="switch" aria-label={`${p.title}启用`} aria-checked={p.enabled} disabled={p.locked || !!p.problem || !!p.blockedReason} title={p.fixed?'历史固定装配':p.locked?'内置插件，保持启用':p.problem || p.blockedReason || (p.enabled?'停用插件':'启用插件')} className="plugin-switch" onClick={()=>onChange('',pluginToggle(version,config,p.name))}><span/>{p.locked && <IconLock size={10}/>}</button></header>
            <p className="plugin-description">{p.summary || p.description}</p>
            <footer><code>{p.name}</code>{target&&<button className="plugin-settings-link" onClick={()=>onOpenSettings(target.id)} title={`打开${target.name}`}>参数<IconArrowUpRight size={12}/></button>}</footer>
            <details className="plugin-metadata"><summary aria-label={`${p.title}详细信息`} title="详细信息"><IconInfoCircle size={15}/></summary>
              <div className="plugin-info-body">
                <p>{p.fixed?'历史固定装配':p.locked?'内置 · 常开':p.enabled?'已启用':'已停用'}</p>
                {p.summary && <p>{p.description}</p>}
                {p.blockedReason && <p>{p.blockedReason}</p>}
                {p.parameterNote && <p>{p.parameterNote}</p>}
                {p.configPath && !target && <p>参数页面待整理，请让 AI 在实验配置中补充。</p>}
                <dl>{[['requires','服务依赖'],['schema_requires','数据依赖'],['includes','包含插件']].map(([key,label])=>p[key]?.length>0&&<React.Fragment key={key}><dt>{label}</dt><dd>{p[key].join('、')}</dd></React.Fragment>)}{p.source&&<><dt>实现来源</dt><dd>{p.source}</dd></>}</dl>
              </div>
            </details>
          </article>;
        })}
      </div>
      {!matching.some(p=>p.category===id)&&<p className="plugin-muted">{query?'没有匹配插件':'此版本未配置此类插件'}</p>}
    </section>)}
  </main>;
}
