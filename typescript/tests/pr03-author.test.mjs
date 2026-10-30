import {test} from 'node:test';import assert from 'node:assert/strict';
import Schema from '../.build/schemastery/dist/index.js';
import {createTestHost} from '../.build/host/dist/testkit.js';
import {pluginFiles} from '../.build/host/dist/scaffold/index.js';
import {slotKey} from '../.build/host/dist/slots/index.js';
const source={package:'@author/generated',version:'0.1.0',gitCommit:'f70aaacdc9559ebb5f43f5ad760d75a3c9eecabb',lockfile:'fixture.lock',patches:[],hostAbi:'society0/host/1'};
test('PR03-T05 scaffold emits literal metadata build inputs and a real consumer test',()=>{
 for(const template of ['service-slot','sqlite-domain','config-bundle']){const files=pluginFiles({name:'@author/generated',template,directory:'unused',source});const manifest=JSON.parse(files['package.json']);assert.equal(manifest.name,'@author/generated');assert.equal(manifest.society.plugin.metadata,'./metadata');assert.match(files['test/consumer.mjs'],/node:test/);assert.match(files['scripts/metadata.mjs'],/plugin.meta.json/);assert(files['src/index.ts'].includes('\n'));}
});
test('command registration drains active invocation and releases run handle on failure',async()=>{
 const host=await createTestHost();const entered=Promise.withResolvers();const finish=Promise.withResolvers();let remove;let released=0;
 const owner=await host.ctx.plugin({inject:['commands'],apply(ctx){remove=ctx.commands.register({name:'slow',scope:'run',summary:'slow',input:Schema.object({text:Schema.string().required()}),async execute(_ctx,input){entered.resolve();await finish.promise;return input.text}})}});
 const invocation={kind:'run',run:{value:{runId:'r',ctx:host.ctx,generation:'g'},async release(){released++}},expectedGeneration:'g',requestId:'1',signal:new AbortController().signal};
 const result=host.ctx.commands.execute('slow',{text:'done'},invocation);await entered.promise;let retired=false;const closing=Promise.resolve(remove()).then(()=>{retired=true});await new Promise(setImmediate);assert.equal(retired,false);finish.resolve();assert.equal(await result,'done');await closing;assert.equal(released,1);
 await assert.rejects(host.ctx.commands.execute('missing',{}, {...invocation,requestId:'2'}),/COMMAND_MISSING/);assert.equal(released,2);await owner.dispose();host.assertQuiescent();await host.close();
});
test('declaration withdrawal ends exact epoch and remounts contributor-owned children',async()=>{
 const host=await createTestHost();const key=slotKey('author/epoch',1);let declaration;const epochs=[];
 const contributor=await host.ctx.plugin({inject:['slots'],apply(ctx){ctx.slots.inject(key,(child,descriptor)=>{epochs.push(descriptor.epoch);child.slots.contribute(key,{id:'answer',value:descriptor.epoch})})}});
 const declare=()=>host.ctx.plugin({inject:['slots'],apply(ctx){declaration=ctx.slots.declare({key,strategy:'single',required:true,validate:value=>value})}});
 const first=await declare();await new Promise(setImmediate);const old=await host.ctx.slots.acquire(key,{generation:'old'});const end=Promise.resolve(declaration());await new Promise(setImmediate);await assert.rejects(host.ctx.slots.acquire(key,{generation:'next'}),/PENDING/);assert.equal(old.items[0].value,epochs[0]);await old.release();await end;
 const next=await declare();await new Promise(setImmediate);const lease=await host.ctx.slots.acquire(key,{generation:'new'});assert.notEqual(lease.epoch,old.epoch);assert.equal(epochs.length,2);await lease.release();await contributor.dispose();await first.dispose();await next.dispose();host.assertQuiescent();await host.close();
});

test('failing run lease release still retires the command execution resource',async()=>{
 const host=await createTestHost();let remove;
 await host.ctx.plugin({inject:['commands'],apply(ctx){remove=ctx.commands.register({name:'release-failure',scope:'run',summary:'release failure',input:Schema.any(),execute(){return 'done'}})}});
 await assert.rejects(host.ctx.commands.execute('release-failure',{}, {kind:'run',run:{value:{runId:'r',ctx:host.ctx},async release(){throw Error('LEASE_RELEASE_FAILURE')}},requestId:'failed',signal:new AbortController().signal}),/LEASE_RELEASE_FAILURE/);
 await remove();const report=host.ctx.resources.closeReport();assert.equal(report.open,0);await host.close();
});

test('first-party sidecar declarations use the actual native Config and required injections',async()=>{
 for(const name of ['module-resolver','config-resolvers','profile-loader','inspector','composition-strategies','slot-strategies','slots','commands','scaffold']){
  const module=await import('../.build/host/dist/'+name+'/index.js');assert.equal(module.default.Config,module.Config,name);assert.equal(module.api.exportPath,'./'+name);assert.equal(module.metadata.hostAbi,'society0/host/1');
  assert.deepEqual(module.api.requires.map(item=>item.service.key),module.default.inject??[],name);
  for(const item of [...module.api.provides,...module.api.requires.map(item=>item.service)]){assert.equal(item.protocol,item.key);assert.equal(item.major,1)}
 }
});
