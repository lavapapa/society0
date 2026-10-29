import {test} from 'node:test';import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile,access} from 'node:fs/promises';import {resolve} from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
import Schema from '@society0/schemastery';
import {Context,Service} from '../.build/host/dist/abi.js';
import {createTestHost} from '../.build/host/dist/testkit.js';
import {compileLayers,mountPlan} from '../.build/host/dist/profile-loader/index.js';
const metadata={title:'Author',summary:'Native',configVersion:1,configPolicy:[],scope:'generation',hostAbi:'society0/host/1'};
const descriptor={key:'counter',protocol:'author/counter',major:1,capabilities:[]};
test('PR03-T04 two research generations use actual private native child trees',async()=>{
 const host=await createTestHost();const seen=[];let sequence=0;
 class Counter extends Service {items=new Set();id=++sequence;constructor(ctx){super(ctx,'counter')}register(value){return this.ctx.effect(()=>{this.items.add(value);return()=>{this.items.delete(value)}})}}
 const bindings={provider:{plugin:Counter,metadata,api:{provides:[descriptor],requires:[]}},consumer:{plugin:{inject:['counter'],apply(ctx){const value={counter:ctx.counter.id,owner:ctx.fiber.uid};seen.push(value);ctx.counter.register(value)}},metadata,api:{provides:[],requires:[{service:descriptor,optional:false,phase:'module'}]}}};
 const runs=['one','two'].map(id=>({id,name:'group',group:true,lifetime:'run',config:[{id:id+'-generation',name:'group',group:true,lifetime:'generation',config:[{id:id+'-provider',name:'provider'},{id:id+'-consumer',name:'consumer'}]}]}));
 const plan=await compileLayers([{kind:'bundle',uri:'fixture',version:'1',patches:[{insert:runs}]}],bindings);
 const mounted=await mountPlan(plan,host.ctx);assert.equal(new Set(seen.map(x=>x.counter)).size,2);
 const provider1=mounted.entries.get('one-provider');const consumer1=mounted.entries.get('one-consumer');assert.equal(consumer1.parent.fiber,mounted.entries.get('one-generation').ctx.fiber);assert.equal(provider1.ctx.counter.items.size,1);
 await mounted.entries.get('one').dispose();assert.equal(mounted.entries.get('two-provider').ctx.counter.items.size,1);await mounted.close();host.assertQuiescent();await host.close();
});
test('prepared schema transform runs once and dump equals mounted effective configuration',async()=>{
 const host=await createTestHost();let observed;const plugin={Config:Schema.transform(Schema.object({count:Schema.number().default(0)}),value=>({count:value.count+1})),apply(_ctx,config){observed=config}};
 const plan=await compileLayers([{kind:'profile',uri:'fixture',version:'1',patches:[{insert:[{id:'one',name:'one',config:{count:5}}]}]}],{one:{plugin,metadata:{...metadata,scope:'host'},api:{provides:[],requires:[]}}});
 assert.equal(plan.flat[0].config.count,6);const tree=await mountPlan(plan,host.ctx);assert.deepEqual(observed,plan.flat[0].evidence.redactedEffective);await tree.close();await host.close();
});
test('PR03-T06 static sidecars stay import-free and runtime validation names import failures',async()=>{
 const packageURL=new URL('../.build/host/package.json',import.meta.url);const host=await createTestHost({baseURL:packageURL.href});const dir=fileURLToPath(new URL('../node_modules/@author/static',packageURL));await mkdir(dir,{recursive:true});
 const source={package:'@author/static',version:'1.0.0',gitCommit:'f70aaacdc9559ebb5f43f5ad760d75a3c9eecabb',lockfile:'fixture.lock',patches:[],hostAbi:'society0/host/1'};
 const manifest={name:source.package,version:source.version,type:'module',exports:{'.':'./index.js','./metadata':'./meta.json','./config-schema':'./schema.json','./api':'./api.json'},peerDependencies:{'@society0/host':'0.1.0'},society:{plugin:{metadata:'./metadata',configSchema:'./config-schema',api:'./api'}}};
 await writeFile(dir+'/package.json',JSON.stringify(manifest));await writeFile(dir+'/index.js',`throw new Error('TOPLEVEL_SENTINEL');export function apply(){}`);
 await writeFile(dir+'/meta.json',JSON.stringify({format:'society-plugin-metadata/v1',package:{name:source.package,version:source.version},source,entries:[{exportPath:'.',metadata:{...metadata,scope:'host'},configSchemaPointer:'#/schema',apiPointer:'#/entries/0'}]}));
 await writeFile(dir+'/schema.json',JSON.stringify({format:'society-config-schema/v1',schema:{},limitations:[]}));await writeFile(dir+'/api.json',JSON.stringify({format:'society-plugin-api/v1',entries:[{exportPath:'.',provides:[],requires:[]}]}));
 const lock={lockfile:'fixture.lock',packages:{[source.package]:source}};const identity=host.ctx.moduleResolver.resolve(source.package,lock);const inspection=host.ctx.moduleResolver.inspect(identity);assert.equal(inspection.mode,'manifest-only');assert.equal(inspection.metadata.package.name,source.package);assert.equal(host.diagnostics.snapshot().errors.length,0);
 await assert.rejects(host.ctx.moduleResolver.importVerified(identity),/RUNTIME_IMPORT.*TOPLEVEL_SENTINEL/);await host.close();
});

test('native injection intercept config survives a prepared mount',async()=>{
 const host=await createTestHost();let observed;
 class Configured extends Service {constructor(ctx){super(ctx,'configured')}read(){return this[Service.resolveConfig]()}}
 const provided=await host.ctx.plugin(Configured);
 const plugin={inject:{configured:{answer:42}},apply(ctx){observed=ctx.configured.read()}};
 const plan=await compileLayers([{kind:'profile',uri:'fixture',version:'1',patches:[{insert:[{id:'consumer',name:'consumer'}]}]}],{consumer:{plugin,metadata:{...metadata,scope:'host'},api:{provides:[],requires:[]}}},{externalServices:['configured']});
 const mounted=await mountPlan(plan,host.ctx);assert.deepEqual(observed,{answer:42});await mounted.close();await provided.dispose();await host.close();
});

test('isolate false resolves the actual native root service key',async()=>{
 const host=await createTestHost();let observed;
 class RootValue extends Service {constructor(ctx){super(ctx,'rootValue')}read(){return 'native-root'}}
 await host.ctx.plugin(RootValue);
 const plugin={inject:['rootValue'],apply(ctx){observed=ctx.rootValue.read()}};
 const plan=await compileLayers([{kind:'profile',uri:'fixture',version:'1',patches:[{insert:[{id:'consumer',name:'consumer',isolate:{rootValue:false}}]}]}],{consumer:{plugin,metadata:{...metadata,scope:'host'},api:{provides:[],requires:[]}}},{externalServices:['rootValue']});
 const tree=await mountPlan(plan,host.ctx);assert.equal(observed,'native-root');await tree.close();await host.close();
});
