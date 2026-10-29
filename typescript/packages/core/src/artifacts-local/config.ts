import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
export const Config=Schema.object({directory:Schema.string().required(),chunkBytes:Schema.natural().min(1).max(1048576).default(65536),maxObjectBytes:Schema.natural().min(1).default(67108864),maxActive:Schema.natural().min(1).default(8)});
export const metadata:PluginMetadata={title:'本地不可变对象',summary:'有界流式封存、范围读取与独立物化',configVersion:1,scope:'run',allowedScopes:['run','bootstrap'],hostAbi:'society0/host/1',configPolicy:['directory','chunkBytes','maxObjectBytes','maxActive'].map(key=>({path:'/'+key,mode:'new-run',affectsResults:true}))};
export const api:ApiEntry={exportPath:'./artifacts-local',provides:[{key:'artifacts',protocol:'artifacts',major:1,capabilities:[]}],requires:[{service:{key:'resources',protocol:'resources',major:1,capabilities:[]},optional:false,phase:'module'}]};
