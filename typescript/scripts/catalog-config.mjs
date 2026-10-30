import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
const root=resolve(new URL('..',import.meta.url).pathname);
const argv=process.argv.slice(2);
const option=name=>{const index=argv.indexOf(name);return index<0?undefined:argv[index+1]};
const output=resolve(option('--out')??join(root,'docs'));
const check=argv.includes('--check');
const read=async path=>JSON.parse(await readFile(path,'utf8'));
const objectFields=schema=>{if(!schema)return [];const node=schema.refs[schema.uid];if(!node||node.type!=='object')return [];return Object.entries(node.dict??{}).map(([name,ref])=>({name,type:schema.refs[ref]?.type??'unknown',default:schema.refs[ref]?.meta?.default??null}));};
const fieldsFor=(schema,policy)=>{const entries=new Map(policy.map(item=>[item.path.split('/')[1],item]));return objectFields(schema).map(field=>({...field,mode:entries.get(field.name)?.mode??null,affectsResults:entries.get(field.name)?.affectsResults??null,secret:entries.get(field.name)?.secret??null}));};
const packages=[];
for(const name of (await read(join(root,'.build/release.json'))).packages){
 const metaPath=join(root,'.build',name,'generated/plugin.meta.json');
 let meta;try{meta=await read(metaPath)}catch{continue}
 const schemas=new Map((await read(join(root,'.build',name,'generated/plugin.schema.json'))).schema.entries.map(entry=>[entry.exportPath,entry.schema]));
 packages.push({package:meta.package.name,entries:meta.entries.map(entry=>({exportPath:entry.exportPath,title:entry.metadata.title,fields:fieldsFor(schemas.get(entry.exportPath),entry.metadata.configPolicy)}))});
}
const config={format:'society-plugin-config/v1',packages};
const json=JSON.stringify(config,null,2)+'\n';
const markdown=['# 插件配置字段（由 `scripts/catalog-config.mjs` 从 .build 侧车生成，勿手工编辑）','',...packages.flatMap(item=>['## '+item.package,'','| 导出 | 字段 | 类型 | 默认值 | 更新方式 | 影响结果 | 机密 |','|---|---|---|---|---|---|---|',...item.entries.flatMap(entry=>entry.fields.map(field=>`| ${entry.exportPath} | ${field.name} | ${field.type} | ${JSON.stringify(field.default)} | ${field.mode} | ${field.affectsResults} | ${field.secret} |`)),''])].join('\n');
if(check){
 const committed=await readFile(join(root,'docs/plugin-config.json'),'utf8').catch(()=>null);
 if(committed!==json){console.error('CATALOG_STALE docs/plugin-config.json');process.exit(1)}
 console.log('catalog current');
}else{
 await mkdir(output,{recursive:true});
 await writeFile(join(output,'plugin-config.json'),json);
 await writeFile(join(output,'plugin-config.md'),markdown);
 console.log(JSON.stringify({packages:packages.length,entries:packages.reduce((total,item)=>total+item.entries.length,0),output}));
}
