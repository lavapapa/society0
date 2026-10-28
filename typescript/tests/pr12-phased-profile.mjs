import assert from 'node:assert/strict';
import {createHost} from '@society0/host';
const host=await createHost({baseURL:import.meta.url,boot:['resources','composition-strategies','slot-strategies','module-resolver','config-resolvers','profile-loader'].map(n=>({specifier:'@society0/host/'+n}))});
try{
 const plan=await host.ctx.profileLoader.compile({format:'society-profile/v2',bundles:[],configSources:[],lockfile:process.argv[2]??'./module-lock.json',patches:[{insert:[{id:'run',name:'run',group:true,lifetime:'run',config:[{id:'slots',name:'@society0/host/slots'},{id:'generation',name:'generation',group:true,lifetime:'generation',config:[{id:'drivers',name:'@society0/core/drivers'}]}]}]}]},{baseURL:import.meta.url});
 const session=await host.ctx.profileLoader.createMountSession(plan,host.ctx);
 await session.mountScopes(['run']);
 const run=session.tree.entries.get('run');
 assert.ok(run.ctx.get('slots'));
 assert.equal(session.tree.entries.has('generation'),false);
 await session.mountScopes(['generation']);
 assert.equal(session.tree.entries.get('run'),run);
 assert.ok(session.tree.entries.get('generation').ctx.get('drivers'));
 await session.disposeScopes(['generation']);
 assert.equal(session.tree.entries.has('drivers'),false);
 assert.ok(run.ctx.get('slots'));
 await session.close();
 assert.equal(session.tree.entries.size,0);
 console.log(JSON.stringify({status:'phased-native-profile-mounted'}));
}finally{await host.close()}
