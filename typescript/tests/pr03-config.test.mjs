import {test} from 'node:test';import assert from 'node:assert/strict';
import Schema from '@society0/schemastery';
import {compileLayers,dumpPlan} from '../.build/host/dist/profile-loader/compiler.js';
import {Context} from '../.build/host/dist/abi.js';
import {ConfigResolverRegistry,envResolver,secretResolver} from '../.build/host/dist/config-resolvers/index.js';
const metadata={title:'Author',summary:'Author service',configVersion:1,configPolicy:[],scope:'host',hostAbi:'society0/host/1'};
const layer=(kind,patches)=>({kind,uri:kind+'.json',version:'1',patches});
const binding=(extra={})=>({plugin:{Config:Schema.object({label:Schema.string().default('default'),count:Schema.number().default(2)}),apply(){}},metadata,api:{exportPath:'.',provides:[],requires:[]},...extra});
test('PR03-T01 pure overlays, schema defaults, references and provenance share effective config',async()=>{
 const ctx=new Context();await ctx.plugin(ConfigResolverRegistry);ctx.configResolvers.register(envResolver({COUNT:'9'}));ctx.configResolvers.register(secretResolver(async()=>({value:'private-value',version:'secret-v1'})));
 const module=binding({plugin:{Config:Schema.object({label:Schema.string().default('default'),count:Schema.number(),token:Schema.string()}),apply(){}}});
 const layers=[layer('bundle',[{insert:[{id:'author',name:'author',config:{count:1,label:'removed'}}]}]),layer('profile',[{id:'author',config:{count:{$ref:{kind:'env',name:'COUNT',parse:'json'}},token:{$ref:{kind:'secret',name:'TOKEN'}}}}])];
 const original=structuredClone(layers);const plan=await compileLayers(layers,{author:module},{resolve:value=>ctx.configResolvers.resolve(value)});
 assert.deepEqual(layers,original);assert.deepEqual(plan.flat[0].config,{count:9,token:'private-value',label:'default'});
 const dump=dumpPlan(plan);assert.equal(dump.entries[0].config.token,'[REDACTED]');assert(!JSON.stringify(dump).includes('private-value'));
 const origins=plan.flat[0].evidence.origins;assert(origins.find(o=>o.effectivePointer==='/count').steps.some(s=>s.operation==='resolve'));assert(origins.find(o=>o.effectivePointer==='/label').steps.some(s=>s.operation==='default'));await ctx.fiber.dispose();
});
test('literal patch adapter rejects unknown, missing, duplicate and executable marker',async()=>{
 const valid=layer('bundle',[{insert:[{id:'author',name:'author',config:{}}]}]);
 for(const patch of [{id:'missing',config:{}},{id:'author',name:'wrong'},{id:'author',unknown:true},{insert:[{id:'author',name:'author'}]},{insert:[{id:'other',name:'author',unexpected:1}]},{id:'author',config:{nested:{__jsExpr:'danger'}}}])await assert.rejects(compileLayers([valid,layer('profile',[patch])],{author:binding()}));
 await assert.rejects(compileLayers([layer('bundle',[{insert:[{id:'x',name:'author',config:{bad:undefined}}]}])],{author:binding()}),/LITERAL/);
});
test('PR03-T04 rejects host requiring generation capability and dependency cycles',async()=>{
 const provider=binding({metadata:{...metadata,scope:'generation'},api:{exportPath:'.',provides:[{key:'short',protocol:'example',major:1,capabilities:[]}],requires:[]}});
 const consumer=binding({api:{exportPath:'.',provides:[],requires:[{service:{key:'short',protocol:'example',major:1,capabilities:[]},optional:false,phase:'module'}]}});
 const entries=[{id:'consumer',name:'consumer'},{id:'run',name:'group',group:true,lifetime:'run',config:[{id:'generation',name:'group',group:true,lifetime:'generation',config:[{id:'provider',name:'provider'}]}]}];
 await assert.rejects(compileLayers([layer('bundle',[{insert:entries}])],{provider,consumer}),/LIFETIME.*consumer.*short.*provider/);
 const service=(provides,required)=>binding({api:{exportPath:'.',provides:[{key:provides,protocol:'x',major:1,capabilities:[]}],requires:[{service:{key:required,protocol:'x',major:1,capabilities:[]},optional:false,phase:'module'}]}});
 await assert.rejects(compileLayers([layer('bundle',[{insert:[{id:'a',name:'a'},{id:'b',name:'b'}]}])],{a:service('a','b'),b:service('b','a')}),/CYCLE.*a.*b|CYCLE.*b.*a/);
});
