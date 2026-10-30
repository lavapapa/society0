import test from 'node:test';import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {mkdtemp,writeFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {fileURLToPath} from 'node:url';import {hostAbi} from '../.build/host/dist/abi.js';
const exec=promisify(execFile),root=fileURLToPath(new URL('..',import.meta.url));
const schemastery=new URL('../.build/schemastery/dist/index.js',import.meta.url).href;
const policy=(...fields)=>fields.map(field=>({path:'/'+field,mode:'new-run',affectsResults:true,secret:false}));
async function fake(expression,entries){
 const directory=await mkdtemp(join(tmpdir(),'sidecar-policy-'));
 await writeFile(join(directory,'index.js'),`import Schema from '${schemastery}';
export const Config = ${expression};
export const metadata = {title:'fake',summary:'fake',configVersion:1,configPolicy:${JSON.stringify(entries)},scope:'run',hostAbi:'${hostAbi}'};
export const api = {exportPath:'.',provides:[],requires:[]};
export default {Config};
`);
 await writeFile(join(directory,'package.json'),JSON.stringify({name:'@society0/fake',version:'0.1.0',type:'module',exports:{'.':{default:'./index.js'}},files:[]}));
 await writeFile(join(directory,'SOURCE.json'),JSON.stringify({package:'@society0/fake',version:'0.1.0'}));
 return directory;
}
async function sidecars(directory,enforce){return exec(process.execPath,[join(root,'scripts/sidecars.mjs'),root,directory,...(enforce?['--enforce-config-policy']:[])],{cwd:root}).catch(error=>error)}
function hits(result){return JSON.parse(result.stdout.split('\n').find(line=>line.startsWith('{"configPolicy"'))).configPolicy}
test('RV3-16-T01 Config 字段与 configPolicy 一致，未声明字段或不可分析时拒绝',async()=>{
 const mismatch=await fake('Schema.object({a: Schema.number()})',[]);
 const enforced=await sidecars(mismatch,true);
 assert.notEqual(enforced.code,0);assert.match(enforced.stderr,/SIDECAR_CONFIG_POLICY/);
 const reported=await sidecars(mismatch,false);
 assert.equal(reported.code??0,0,String(reported.stderr));
 assert.ok(hits(reported).some(item=>item.startsWith('SIDECAR_CONFIG_POLICY')));
 const unanalyzable=await fake('{}',[]);
 const failed=await sidecars(unanalyzable,true);
 assert.notEqual(failed.code,0);assert.match(failed.stderr,/SIDECAR_CONFIG_UNANALYZABLE/);
 const consistent=await fake('Schema.intersect([Schema.object({a: Schema.number()}), Schema.object({b: Schema.number()})])',policy('a','b'));
 const passed=await sidecars(consistent,true);
 assert.equal(passed.code??0,0,String(passed.stderr));
 assert.equal(hits(passed).length,0);
});
