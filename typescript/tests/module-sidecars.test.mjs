import {test} from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {createRequire} from 'node:module';
import {createHost,hostAbi} from '@society0/host';import {ModuleResolver} from '@society0/host/module-resolver';
const require=createRequire(import.meta.url);
const pointer=(value,path)=>path.slice(2).split('/').reduce((node,key)=>node[key.replaceAll('~1','/').replaceAll('~0','~')],value);
test('published Host and Core sidecars resolve and import their actual declared plugin contracts',async()=>{
 const packages=await Promise.all(['@society0/host','@society0/core'].map(async name=>JSON.parse(await readFile(require.resolve(name+'/plugin.meta.json'),'utf8'))));
 const lock={lockfile:'outside-package-lock',packages:Object.fromEntries(packages.map(meta=>[meta.package.name,{version:meta.package.version,gitCommit:meta.source.gitCommit,dirty:meta.source.dirty,patches:meta.source.patches,hostAbi}]))};
 const host=await createHost({boot:[],baseURL:import.meta.url});let inspected=0;
 try{
  await host.ctx.plugin(ModuleResolver,{baseURL:import.meta.url});
  for(const meta of packages)for(const entry of meta.entries){
   const specifier=meta.package.name+(entry.exportPath==='.'?'':entry.exportPath.slice(1));
   const identity=host.ctx.moduleResolver.resolve(specifier,lock),inspection=host.ctx.moduleResolver.inspect(identity);
   assert.equal(inspection.mode,'manifest-only');assert.ok(pointer(inspection.configSchema,entry.configSchemaPointer));
   const loaded=await host.ctx.moduleResolver.importVerified(identity);
   assert.equal(loaded.plugin.Config,loaded.namespace.Config);assert.deepEqual(pointer(inspection.api,entry.apiPointer),loaded.namespace.api);assert.equal(loaded.namespace.metadata.hostAbi,hostAbi);inspected++;
  }
  assert.equal(inspected,packages.reduce((count,meta)=>count+meta.entries.length,0));assert.ok(inspected>=17);
  const wrong={...lock,packages:{...lock.packages,'@society0/core':{...lock.packages['@society0/core'],gitCommit:'wrong-source'}}};
  const identity=host.ctx.moduleResolver.resolve('@society0/core/sqlite',wrong);assert.throws(()=>host.ctx.moduleResolver.inspect(identity),/METADATA_IDENTITY/);
  const wrongDirty=structuredClone(lock);wrongDirty.packages['@society0/core'].dirty=!lock.packages['@society0/core'].dirty;
  const dirtyIdentity=host.ctx.moduleResolver.resolve('@society0/core/sqlite',wrongDirty);assert.throws(()=>host.ctx.moduleResolver.inspect(dirtyIdentity),/METADATA_IDENTITY/);
  const missingDirty=structuredClone(lock);delete missingDirty.packages['@society0/core'].dirty;
  assert.throws(()=>host.ctx.moduleResolver.resolve('@society0/core/sqlite',missingDirty),/SOURCE_LOCK/);
 }finally{await host.close()}
});

// cases: RV3-01-T04
