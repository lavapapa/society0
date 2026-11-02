// cases: RV3-01-T04
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
import {createTestHost} from '../.build/host/dist/testkit.js';
const root=fileURLToPath(new URL('..',import.meta.url));

test('module and bundle locks require dirty and preserve matching dirty runtime identities',async()=>{
 const probe=await mkdtemp(join(tmpdir(),'g18-module-dirty-'));
 const directory=join(probe,'node_modules/dirty-probe');await mkdir(directory,{recursive:true});
 await symlink(resolve(root,'.build/node_modules/@society0'),join(probe,'node_modules/@society0'),'dir');
 const save=(file,value)=>writeFile(join(directory,file),JSON.stringify(value));
 await writeFile(join(probe,'package.json'),JSON.stringify({name:'dirty-consumer',version:'1.0.0',type:'module'}));
 const actual=JSON.parse(await readFile(resolve(root,'.build/host/generated/plugin.meta.json'),'utf8')).source;
 const source={...actual,package:'dirty-probe',version:'1.0.0',dirty:false};
 const lock={lockfile:'probe.lock.json',packages:{'dirty-probe':source}};
 const metadata={format:'society-plugin-metadata/v1',package:{name:'dirty-probe',version:'1.0.0'},source,entries:[]};
 await save('package.json',{name:'dirty-probe',version:'1.0.0',type:'module',peerDependencies:{'@society0/host':'0.1.0'},exports:{'./package.json':'./package.json','./plugin':'./plugin.js','./bundle':'./bundle.json','./meta':'./meta.json','./schema':'./schema.json','./api':'./api.json'},society:{bundle:{patch:'./bundle'},plugin:{metadata:'./meta',configSchema:'./schema',api:'./api'}}});
 await writeFile(join(directory,'plugin.js'),'export default function apply(){}');
 await save('bundle.json',[]);await save('meta.json',metadata);
 await save('schema.json',{format:'society-config-schema/v1',limitations:[]});
 await save('api.json',{format:'society-plugin-api/v1',entries:[]});
 const host=await createTestHost({baseURL:pathToFileURL(join(probe,'package.json')).href});
 try{
  const resolver=host.ctx.moduleResolver,identity=resolver.resolve('dirty-probe/plugin',lock);
  assert.equal(identity.source.dirty,false);resolver.inspect(identity);
  for(const dirty of [undefined,null,'false']){
   const invalid={...lock,packages:{'dirty-probe':{...source,dirty}}};
   assert.throws(()=>resolver.resolve('dirty-probe/plugin',invalid),/SOURCE_LOCK/);
   assert.throws(()=>resolver.bundle('dirty-probe','1.0.0',invalid),/BUNDLE_LOCK/);
  }
  await save('meta.json',{...metadata,source:{...source,dirty:true}});
  assert.throws(()=>resolver.inspect(identity),/METADATA_IDENTITY/);
  const dirtyLock={...lock,packages:{'dirty-probe':{...source,dirty:true}}};
  const dirtyIdentity=resolver.resolve('dirty-probe/plugin',dirtyLock);
  assert.equal(dirtyIdentity.source.dirty,true);resolver.inspect(dirtyIdentity);
  resolver.bundle('dirty-probe','1.0.0',dirtyLock);
 }finally{await host.close()}
 console.log(JSON.stringify({case:'RV3-01-T04',fixture:probe}));
});
