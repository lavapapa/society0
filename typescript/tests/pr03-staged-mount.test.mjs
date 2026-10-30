import {test} from 'node:test';import assert from 'node:assert/strict';import {setImmediate} from 'node:timers/promises';
import {Service} from '../.build/host/dist/abi.js';import {createTestHost} from '../.build/host/dist/testkit.js';import {compileLayers} from '../.build/host/dist/profile-loader/index.js';
const service={key:'runState',protocol:'runState',major:1,capabilities:[]};
const meta=scope=>({title:scope,summary:'Native staged profile fixture',configVersion:1,configPolicy:[],scope,hostAbi:'society0/host/1'});
test('full run/bootstrap/generation groups retain native parents and aliases across physical stage disposal',async()=>{
 const host=await createTestHost(),gate=Promise.withResolvers();let starts=0,cleanupStarted=false;const seen=[];
 class RunState extends Service {events=[];constructor(ctx){super(ctx,'runState');starts++}record(value){this.events.push(value)}read(){return [...this.events]}}
 const bindings={state:{plugin:RunState,metadata:meta('run'),api:{provides:[service],requires:[]}},bootstrap:{plugin:{inject:['runState'],apply(ctx){ctx.runState.record('prepare');ctx.effect(()=>async()=>{cleanupStarted=true;await gate.promise;ctx.runState.record('bootstrap-closed')})}},metadata:meta('bootstrap'),api:{provides:[],requires:[{service,optional:false,phase:'module'}]}},generation:{plugin:{inject:['runState'],apply(ctx){seen.push({events:ctx.runState.read(),parent:ctx.fiber.parent.fiber})}},metadata:meta('generation'),api:{provides:[],requires:[{service,optional:false,phase:'module'}]}}};
 const plan=await compileLayers([{kind:'profile',uri:'full-groups.json',version:'1',patches:[{insert:[{id:'run',name:'run',group:true,lifetime:'run',isolate:{runState:'retained-run-alias'},config:[{id:'state',name:'state'},{id:'bootstrap',name:'bootstrap',group:true,lifetime:'bootstrap',config:[{id:'prepare',name:'bootstrap'}]},{id:'generation',name:'generation',group:true,lifetime:'generation',config:[{id:'consumer',name:'generation'}]}]}]}]}],bindings);
 let session;
 try {session=await host.ctx.profileLoader.createMountSession(plan,host.ctx);const tree=await session.mountScopes(['run','bootstrap']);const run=tree.entries.get('run');assert.equal(starts,1);assert.equal(tree.entries.has('generation'),false);assert.equal(tree.entries.get('bootstrap').parent.fiber,run.ctx.fiber);
  const releasing=session.disposeScopes(['bootstrap']);const mounting=session.mountScopes(['generation']);await setImmediate();assert.equal(cleanupStarted,true);assert.equal(seen.length,0);gate.resolve();await releasing;assert.equal(await mounting,tree);
  assert.equal(tree.entries.has('bootstrap'),false);assert.equal(tree.entries.get('generation').parent.fiber,run.ctx.fiber);assert.equal(seen[0].parent,tree.entries.get('generation').ctx.fiber);assert.deepEqual(seen[0].events,['prepare','bootstrap-closed']);assert.equal(starts,1);assert.throws(()=>host.ctx.profileLoader.inspectConfig('prepare'),/CONFIG_NOT_MOUNTED/);
  await session.disposeScopes(['generation']);await session.mountScopes(['generation']);assert.equal(starts,1);assert.equal(seen.length,2);await session.close();assert.equal(tree.entries.size,0);host.assertQuiescent();
 }finally{gate.resolve();await session?.close();await host.close()}
});


test('public Core Drivers mounts below the retained run group while host Slots stays separate',async()=>{
 const {createHost}=await import('../.build/host/dist/index.js');const slots=await import('../.build/host/dist/slots/index.js');const drivers=await import('../.build/core/dist/drivers/index.js');
 const host=await createHost({baseURL:new URL('../.build/host/package.json',import.meta.url).href,boot:['resources','composition-strategies','slot-strategies','module-resolver','config-resolvers','profile-loader','inspector'].map(name=>({specifier:'@society0/host/'+name}))});
 try {
  const plan=await compileLayers([{kind:'profile',uri:'public-all-groups.json',version:'1',patches:[{insert:[{id:'host',name:'host',group:true,lifetime:'host',config:[{id:'host-slots',name:'slots'},{id:'run',name:'run',group:true,lifetime:'run',config:[{id:'run-slots',name:'slots'},{id:'generation',name:'generation',group:true,lifetime:'generation',config:[{id:'drivers',name:'drivers'}]}]}]}]}]}],{slots:{plugin:slots.default,metadata:slots.metadata,api:slots.api},drivers:{plugin:drivers.default,metadata:drivers.metadata,api:drivers.api}},{externalServices:['resources','compositionStrategies']});
  const session=await host.ctx.profileLoader.createMountSession(plan,host.ctx);const tree=await session.mountScopes(['host']);const hostGroup=tree.entries.get('host');assert.equal(tree.entries.has('run'),false);assert.equal(host.ctx.inspector.explain({entryId:'drivers'}).status,'unmounted');
  await session.mountScopes(['run','bootstrap']);const run=tree.entries.get('run');assert.equal(run.parent.fiber,hostGroup.ctx.fiber);await session.disposeScopes(['bootstrap']);await session.mountScopes(['generation']);
  assert.equal(tree.entries.get('generation').parent.fiber,run.ctx.fiber);assert.equal(tree.entries.get('drivers').parent.fiber,tree.entries.get('generation').ctx.fiber);
  const registry=tree.entries.get('drivers').ctx.drivers;registry.register('staged-fixture',{activate:async()=>42});const lease=await registry.acquire('staged-fixture',{generation:'staged-fixture'});assert.equal(await lease.value.activate(),42);await lease.release();
  assert.equal(tree.entries.get('host-slots').ctx.slots.describe().length,0);assert.ok(tree.entries.get('run-slots').ctx.slots.describe().length>0);assert.equal(host.ctx.inspector.explain({entryId:'drivers'}).status,'active');
  await session.disposeScopes(['generation']);assert.equal(tree.entries.get('run-slots').ctx.slots.describe().length,0);await session.close();
 }finally{await host.close()}
});
