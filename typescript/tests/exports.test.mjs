import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHost} from '../.build/host/dist/index.js';
test('real third-party conditional array and pattern exports share Node ESM resolution',async()=>{
 const directory=resolve('node_modules/@fixtures/export-maps');await mkdir(directory+'/plugins',{recursive:true});
 await writeFile(directory+'/package.json',JSON.stringify({name:'@fixtures/export-maps',version:'1.0.0',type:'module',peerDependencies:{'@society0/host':'^0.1.0'},exports:{'.':{types:'./types.d.ts',node:{import:'./entry.mjs',require:'./wrong.cjs'},default:'./wrong.mjs'},'./array':['./entry.mjs','./wrong.mjs'],'./plugins/*':'./plugins/*.mjs'}}));
 await writeFile(directory+'/entry.mjs',`export function apply(ctx){ctx.provide('exportProbe','condition-array')}`);
 await writeFile(directory+'/plugins/example.mjs',`export function apply(ctx){ctx.provide('exportProbe','pattern')}`);
 for(const [specifier,expected] of [['@fixtures/export-maps','condition-array'],['@fixtures/export-maps/array','condition-array'],['@fixtures/export-maps/plugins/example','pattern']]){
  assert.equal(typeof (await import(specifier)).apply,'function');
  const host=await createHost({boot:[{specifier}],baseURL:pathToFileURL(resolve('entry.mjs')).href});
  try{assert.equal(host.ctx.get('exportProbe'),expected)}finally{await host.close()}
 }
});
