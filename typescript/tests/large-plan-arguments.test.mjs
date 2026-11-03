import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {argumentsFor,largeProfile,sameProcessCode} from './pr12-large-plan.mjs';
for(const value of [undefined,'0','-1','1.5','nan','9007199254740992'])test('079 rejects invalid timeout '+value,()=>{
 const args=['consumer','fresh'];if(value!==undefined)args.push('--timeout-ms='+value);assert.throws(()=>argumentsFor(args),/Usage: node pr12-large-plan.mjs/);
});
test('079 requires two paths and accepts explicit safe timeout',()=>{
 assert.throws(()=>argumentsFor(['a','b','extra','--timeout-ms=1']),/Usage/);assert.deepEqual(argumentsFor(['a','b','--timeout-ms=1800000']),{consumer:resolve('a'),output:resolve('b'),timeoutMs:1800000});
});
test('079 authentic rule profile expands beyond1MiB with1000 actors and one step',async()=>{
 const parent=resolve(tmpdir()),consumer=await mkdtemp(join(parent,'g20-large-input-'));assert.equal(resolve(consumer,'..'),parent);
 try{
  await mkdir(join(consumer,'profiles'));await writeFile(join(consumer,'profiles/rule-sqlite.json'),await readFile(new URL('../profiles/rule-sqlite.json',import.meta.url)));const text=await largeProfile(consumer),profile=JSON.parse(text);assert(Buffer.byteLength(text)>1048576);const actors=profile.patches.find(entry=>entry.id==='plain-init').config.initialActors;assert.equal(actors.length,1000);assert.equal(actors[0].attributes.body.length,128);assert.equal(profile.patches.find(entry=>entry.id==='proposal-sequence').config.proposals.length,1);const code=sameProcessCode({profile:'source'}, {targetProfile:'target'});assert(code.includes("import('./examples/run-profile.mjs')"));assert(code.includes("import('./examples/run-profile-branch.mjs')"));
 }finally{assert.equal(resolve(consumer,'..'),parent);await rm(consumer,{recursive:true})}
});
// cases: PR12-T03 RV3-11-T02
