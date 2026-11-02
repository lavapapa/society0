// cases: RV3-01-T03
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root=fileURLToPath(new URL('..',import.meta.url));
const cases=[{name:'unknown',error:'SOURCE_DIRTY_UNKNOWN'},{name:'invalid',dirty:'false',error:'SOURCE_DIRTY_UNKNOWN'},{name:'dirty',dirty:true,error:'DIRTY_PACKAGE'},{name:'clean',dirty:false},{name:'allowed',dirty:true,allow:true}];
for(const item of cases)test('profile-lock '+item.name,async()=>{
 const probe=await mkdtemp(join(tmpdir(),'g18-profile-lock-'));
 const script=join(probe,'profile-lock.mjs');await copyFile(resolve(root,'examples/profile-lock.mjs'),script);
 for(const name of ['host','core']){
  const directory=join(probe,'node_modules/@society0',name);await mkdir(directory,{recursive:true});
  const actual=JSON.parse(await readFile(resolve(root,`.build/${name}/generated/plugin.meta.json`),'utf8'));
  const source={...actual.source,dirty:name==='host'?item.dirty:false};
  await writeFile(join(directory,'package.json'),JSON.stringify({name:'@society0/'+name,version:actual.package.version,exports:{'./package.json':'./package.json','./plugin.meta.json':'./plugin.meta.json'},society:{plugin:{metadata:'./plugin.meta.json'}}}));
  await writeFile(join(directory,'plugin.meta.json'),JSON.stringify({source}));
 }
 const output=join(probe,'lock.json');
 const run=spawnSync(process.execPath,[script,output,'@society0/host','@society0/core',...(item.allow?['--allow-dirty']:[])],{cwd:probe,encoding:'utf8'});
 await writeFile(join(probe,'result.log'),run.stdout+run.stderr);
 if(item.error){assert.notEqual(run.status,0);assert.ok(run.stderr.includes(item.error),run.stderr)}
 else{
  assert.equal(run.status,0,run.stderr);const lock=JSON.parse(await readFile(output,'utf8'));
  assert.equal(lock.packages['@society0/host'].dirty,item.dirty);assert.equal(lock.packages['@society0/core'].dirty,false);
 }
 console.log(JSON.stringify({case:'RV3-01-T03',fixture:probe}));
});
