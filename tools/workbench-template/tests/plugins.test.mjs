import {test} from 'node:test';
import assert from 'node:assert/strict';
import {pluginsFor, pluginToggle, pluginSettingsTarget} from '../src/plugins.js';
import {changesBetween, changeRequest} from '../src/model.js';

const version={id:'v1',config:{observer:{enabled:true,params:{limit:3}}},plugins:[
 {name:'storage',requires:[],schema_requires:[],includes:[]},
 {name:'observer',requires:['storage'],schema_requires:[],includes:[]}
],pluginViews:{storage:{category:'builtin',title:'共享存储'},observer:{category:'social',title:'推荐观测',enabledPath:'/observer/enabled',configPath:'/observer/params'}}};
test('插件参数入口选择最具体的实验配置页',()=>{
 const v={entities:[{id:'all',kind:'overview',configPath:'/environment'},
  {id:'memory',kind:'overview',configPath:'/environment/memory'}]};
 assert.equal(pluginSettingsTarget(v,{configPath:'/environment/memory/limit'}).id,'memory');
 assert.equal(pluginSettingsTarget(v,{configPath:'/missing'}),null);
 assert.equal(pluginSettingsTarget(v,{}),null);
});
test('内置开关保持开启，社交扩展开关写入真实配置差异',()=>{
 const plugins=pluginsFor(version,version.config);
 assert.equal(plugins[0].enabled,true);
 assert.equal(plugins[0].locked,true);
 assert.throws(()=>pluginToggle(version,version.config,'storage'),/内置/);
 const next=pluginToggle(version,version.config,'observer');
 assert.equal(next.observer.enabled,false);
 assert.equal(version.config.observer.enabled,true);
 const request=changeRequest(version,changesBetween(version.config,next));
 assert.deepEqual(request.changes,[{op:'replace',path:'/observer/enabled',before:true,after:false}]);
 assert.match(request.instruction,/暂时不运行/);
 assert.equal(pluginsFor(version,next)[1].enabled,false);
});
test('只呈现该版本的插件，缺少开关绑定明确显示问题',()=>{
 assert.deepEqual(pluginsFor({plugins:[]},{}),[]);
 const broken={...version,pluginViews:{observer:{category:'social'}}};
 assert.match(pluginsFor(broken,version.config)[1].problem,/启停字段/);
 assert.throws(()=>pluginToggle(broken,version.config,'observer'),/启停字段/);
 assert.equal(pluginsFor({...version,plugins:[version.plugins[0]]},version.config).length,1);
});

test('参数路径被删除时提示核对，缺少分类时不猜测类别',()=>{
 const next=structuredClone(version.config);delete next.observer.params;
 assert.match(pluginsFor(version,next)[1].problem,/参数路径/);
 assert.match(pluginsFor({...version,pluginViews:{}},version.config)[0].problem,/分类/);
});

test('历史固定装配显示真实启用状态，保持只读',()=>{
 const historical={...version,pluginViews:{...version.pluginViews,observer:{category:'social',fixed:true,enabled:true}}};
 const item=pluginsFor(historical,version.config)[1];
 assert.equal(item.enabled,true);assert.equal(item.locked,true);assert.equal(item.problem,'');
 assert.throws(()=>pluginToggle(historical,version.config,'observer'),/历史/);
});

test('依赖组合停用时，来源显示停用并锁定；恢复组合后保留来源选择',()=>{
 const grouped={...version,config:{...version.config,social:false},pluginViews:{...version.pluginViews,
  observer:{...version.pluginViews.observer,requiresEnabledPaths:['/social']}}};
 const item=pluginsFor(grouped,grouped.config)[1];
 assert.equal(item.enabled,false);
 assert.match(item.blockedReason,/依赖/);
 assert.throws(()=>pluginToggle(grouped,grouped.config,'observer'),/依赖/);
 assert.equal(pluginsFor(grouped,{...grouped.config,social:true})[1].enabled,true);
});
