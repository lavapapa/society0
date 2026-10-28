import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, cp } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { createHost, Context, Service, FiberState } from '@society0/host';
import { Service as NativeService } from '@society0/cordis';
import { Service as AbiService } from '@society0/host/abi';
import { getSourceIdentity } from '@society0/host/source';
import { Entries } from '@fixtures/provider/service';
const baseURL = import.meta.url;
const boot = ['@fixtures/provider','@fixtures/consumer','@fixtures/jsdoc'].map(specifier => ({specifier}));
async function fixture(name, source, extra={}) {
  const dir=resolve('node_modules',name);
  await mkdir(dir,{recursive:true});
  await writeFile(resolve(dir,'package.json'), JSON.stringify({name,version:'1.0.0',type:'module',exports:{'.':'./index.js'},peerDependencies:{'@society0/host':'^0.1.0'},...extra}));
  await writeFile(resolve(dir,'index.js'),source);
  return dir;
}
const source = `globalThis.__imports=(globalThis.__imports??0)+1; export function apply(){globalThis.__applies=(globalThis.__applies??0)+1}`;
test('PR01-T01 three independently packed plugins share native identity and caller-owned cleanup', async () => {
  assert.equal(typeof FiberState.ACTIVE,"number");
  assert.equal(Service,NativeService); assert.equal(Service,AbiService);
  assert(Entries.prototype instanceof Service);
  const host = await createHost({boot,baseURL});
  assert(host.ctx instanceof Context);
  const registry=host.ctx.entries;
  assert.deepEqual([...registry.entries.keys()],['typescript','jsdoc']);
  assert.deepEqual([...registry.entries.values()],[host.fibers[1].uid,host.fibers[2].uid]);
  assert.equal(registry.answer(21),42);
  await host.fibers[1].dispose();
  assert.deepEqual([...registry.entries.keys()],['jsdoc']);
  await host.fibers[2].dispose();
  assert.equal(registry.entries.size,0);
  await host.close(); await host.close();
  assert.equal(host.ctx.registry.size,0);
  assert.equal(host.ctx.fiber.children.size,0);
  assert.equal(host.ctx.fiber._disposables.length,0);
});
test('PR01-T02 default/apply ambiguity fails before every boot apply', async () => {
  globalThis.__imports=0;globalThis.__applies=0;
  await fixture('@fault/valid',source);
  await fixture('@fault/ambiguous',source+';export default ()=>{}');
  await assert.rejects(createHost({boot:[{specifier:'@fault/valid'},{specifier:'@fault/ambiguous'}],baseURL}), /PLUGIN_ENTRY_AMBIGUOUS.*@fault\/ambiguous/);
  assert.equal(globalThis.__applies,0);
});
test('PR01-T02 missing export artifact fails before ANY module import', async () => {
  globalThis.__imports=0;globalThis.__applies=0;
  await fixture('@fault/import-marker',source);
  const dir=await fixture('@fault/missing',source,{exports:{'.':'./absent.js'}});
  await assert.rejects(createHost({boot:[{specifier:'@fault/import-marker'},{specifier:'@fault/missing'}],baseURL}), error => {
    assert.match(error.message,/PACKAGE_EXPORTS.*@fault\/missing/);assert(error.message.includes(dir+'/absent.js'));return true;
  });
  assert.equal(globalThis.__imports,0);assert.equal(globalThis.__applies,0);
});
test('PR01-T02 undeclared subpath is rejected without importing the package', async () => {
  globalThis.__imports=0;
  await fixture('@fault/no-subpath',source);
  await assert.rejects(createHost({boot:[{specifier:'@fault/no-subpath/private'}],baseURL}), /PACKAGE_EXPORTS.*private/);
  assert.equal(globalThis.__imports,0);
});
test('PR01-T03 physical framework duplicate reports both paths before apply', async () => {
  globalThis.__imports=0;globalThis.__applies=0;
  const dir=await fixture('@fault/duplicate',source);
  const original=resolve('node_modules/@society0/cordis');
  const duplicate=resolve(dir,'node_modules/@society0/cordis');
  await cp(original,duplicate,{recursive:true});
  const copy=await import(pathToFileURL(resolve(duplicate,'dist/index.js')));
  assert.notEqual(copy.Service,Service);
  await assert.rejects(createHost({boot:[{specifier:'@fault/duplicate'}],baseURL}), error => {
    assert.match(error.message,/FRAMEWORK_DUPLICATE/); assert(error.message.includes(original));assert(error.message.includes(duplicate));return true;
  });
  assert.equal(globalThis.__imports,0);assert.equal(globalThis.__applies,0);
});
test('PR01-T03 incompatible host peer reports source and resolved paths before apply', async () => {
  globalThis.__imports=0;globalThis.__applies=0;
  const dir=await fixture('@fault/peer',source,{peerDependencies:{'@society0/host':'^99.0.0'}});
  await assert.rejects(createHost({boot:[{specifier:'@fault/peer'}],baseURL}), error => {
    assert.match(error.message,/PEER_CONFLICT.*\^99\.0\.0/);assert(error.message.includes(dir));assert(error.message.includes(resolve('node_modules/@society0/host')));return true;
  });
  assert.equal(globalThis.__imports,0);assert.equal(globalThis.__applies,0);
});
test('PR01-T04 pure boot installs no optional loader, model or database', async () => {
  const host=await createHost({boot:[],baseURL});
  assert.equal(host.ctx.registry.size,0);
  for(const name of ['loader','model','database','sqlite','inference']) assert.equal(host.ctx.get(name),undefined);
  await host.close();
});
test('source identity records upstream commit and ordered native patch', () => {
  const identity=getSourceIdentity();
  assert.equal(identity.commit,'22ddee3792402c3e70ec5f86a63c3e3e895a2f60');
  assert.equal(identity.patches[0].order,1); assert.match(identity.patches[0].file,/native-lifecycle/);
});
test('failed startup drains resources from earlier boot plugins', async () => {
  globalThis.__owned=0;
  await fixture('@fault/resource',`export function apply(ctx){globalThis.__owned++;ctx.effect(()=>()=>{globalThis.__owned--})}`);
  await fixture('@fault/pending',`export const inject=['absentCapability'];export function apply(){throw Error('unexpected activation')}`);
  await assert.rejects(createHost({boot:[{specifier:'@fault/resource'},{specifier:'@fault/pending'}],baseURL}),/BOOT_NOT_ACTIVE.*absentCapability/);
  assert.equal(globalThis.__owned,0);
});
