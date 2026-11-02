import {test} from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
const base=new URL('../.build/host/',import.meta.url);
const json=async path=>JSON.parse(await readFile(new URL(path,base),'utf8'));
test('host public sidecars contain actual provider schemas and source evidence',async()=>{
 const pkg=await json('package.json');assert.ok(pkg.society?.plugin,'published sidecar declaration is required');
 const load=key=>json(pkg.exports[pkg.society.plugin[key]]);const meta=await load('metadata'),schema=await load('configSchema'),api=await load('api');
 assert.equal(meta.package.name,pkg.name);assert.ok(meta.source.gitCommit);assert.equal(typeof meta.source.dirty,'boolean');assert.deepEqual(meta.source.patches,['0001-native-lifecycle.patch','0002-include-generic-types.patch']);
 for(const name of ['resources','tasks','diagnostics']){
  const namespace=await import(new URL(`dist/${name}/index.js`,base));assert.equal(namespace.default.Config,namespace.Config);
  const index=meta.entries.findIndex(entry=>entry.exportPath==='./'+name);assert.ok(index>=0,name);
  const shape=schema.schema.entries[index].schema;assert.deepEqual(Object.keys(shape.refs[shape.uid].dict),Object.keys(namespace.Config.dict));
  assert.equal(api.entries[index].provides[0].protocol,name);
 }
});
