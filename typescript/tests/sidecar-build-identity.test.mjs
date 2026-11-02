// cases: RV3-01-T01
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,cp,symlink,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync,execFileSync} from 'node:child_process';
const root=fileURLToPath(new URL('..',import.meta.url));

test('sidecars consume stamped implementation and preserve its evidence fields',async()=>{
 const probe=await mkdtemp(join(tmpdir(),'g18-sidecar-identity-'));
 const directory=join(probe,'.build/host');
 await mkdir(join(probe,'.build'),{recursive:true});
 await cp(resolve(root,'.build/host'),directory,{recursive:true});
 await symlink(resolve(root,'.build/node_modules'),join(probe,'.build/node_modules'),'dir');
 await symlink(resolve(root,'node_modules'),join(probe,'node_modules'),'dir');
 await mkdir(join(probe,'patches'));
 await cp(resolve(root,'patches/series.json'),join(probe,'patches/series.json'));
 await writeFile(join(probe,'.gitignore'),'.build/\nnode_modules\n');
 const git=(...args)=>execFileSync('git',args,{cwd:probe,encoding:'utf8'}).trim();
 git('init','-q');git('add','.');git('-c','user.name=Sidecar probe','-c','user.email=probe@invalid','-c','commit.gpgsign=false','commit','-qm','sidecar context');
 const provenance=JSON.parse(await readFile(join(directory,'SOURCE.json'),'utf8'));
 // 给真实构建Host副本提供明确的生产者合同输入，容器Git身份独立存在。
 const stamp={...provenance.implementation,dirty:true,release:false,dirtyPaths:['fixture-source.txt']};
 assert.notEqual(git('rev-parse','HEAD'),stamp.gitCommit);
 await writeFile(join(probe,'fixture-source.txt'),'working input\n');
 provenance.implementation=stamp;
 await writeFile(join(directory,'SOURCE.json'),JSON.stringify(provenance));
 const run=()=>spawnSync(process.execPath,[resolve(root,'scripts/sidecars.mjs'),probe,directory],{encoding:'utf8',maxBuffer:16*1024*1024});
 const first=run();await writeFile(join(probe,'producer.log'),first.stdout+first.stderr);
 assert.equal(first.status,0,first.stderr);
 const meta=JSON.parse(await readFile(join(directory,'generated/plugin.meta.json'),'utf8'));
 assert.equal(meta.source.gitCommit,stamp.gitCommit);
 assert.equal(meta.source.dirty,true);
 const saved=JSON.parse(await readFile(join(directory,'SOURCE.json'),'utf8'));
 for(const [key,value] of Object.entries(stamp))assert.deepEqual(saved.implementation[key],value,key);
 const schema=JSON.parse(await readFile(join(directory,'generated/plugin.schema.json'),'utf8'));
 assert.deepEqual(schema.limitations.find(item=>item.code==='DIRTY_WORKTREE').paths,stamp.dirtyPaths);
 delete provenance.implementation;
 await writeFile(join(directory,'SOURCE.json'),JSON.stringify(provenance));
 const missing=run();await writeFile(join(probe,'missing.log'),missing.stdout+missing.stderr);
 assert.notEqual(missing.status,0);assert.match(missing.stderr,/SIDECAR_IMPLEMENTATION/);
 console.log(JSON.stringify({case:'RV3-01-T01',fixture:probe}));
});
