import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
import { applyEntryPatches } from '../.build/include/dist/index.js';
test('generic include patch emits exactly the original JavaScript',async()=>{
  const [before,after]=await Promise.all(['../vendor-baseline/include/src/index.ts','../vendor/include/src/index.ts'].map(path=>readFile(new URL(path,import.meta.url),'utf8')));
  const emit=source=>ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2024,module:ts.ModuleKind.ESNext,removeComments:true}}).outputText;
  assert.equal(emit(after),emit(before));
});
test('real include keeps custom isolate false and typed nested insert data',()=>{
  const original=[{id:'group',name:'group',group:true,config:[{id:'one',name:'probe',isolate:{database:false},config:{weight:1}}]}];
  const result=applyEntryPatches(original,[{id:'one',isolate:{database:false},config:{weight:2}},{id:'group',insert:[{id:'two',name:'probe',isolate:{database:'local'}}]},{id:'two',disabled:true}],()=>{throw Error('unexpected skipped patch')});
  assert.equal(result[0].config[0].isolate.database,false);assert.equal(result[0].config[0].config.weight,2);
  assert.equal(result[0].config[1].disabled,true);assert.equal(original[0].config.length,1);assert.equal(original[0].config[0].config.weight,1);
});
