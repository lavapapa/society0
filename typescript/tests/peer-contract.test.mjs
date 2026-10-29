import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {createHost} from '@society0/host';
test('required missing and malformed peers fail before import; absent optional peer is accepted',async()=>{
  for(const [name,peers,optional,expected] of [
    ['missing',{'@society0/host':'^0.1.0','@fixtures/absent-peer':'1.0.0'},false,/PEER_CONFLICT.*resolved missing/],
    ['malformed',{'@society0/host':'not-a-semver'},false,/PEER_CONFLICT/],
    ['optional',{'@society0/host':'^0.1.0','@fixtures/absent-peer':'1.0.0'},true,null],
  ]){
    const specifier='@fixtures/peer-'+name;const directory=resolve('node_modules',specifier);
    await mkdir(directory,{recursive:true});
    await writeFile(resolve(directory,'package.json'),JSON.stringify({name:specifier,version:'1.0.0',type:'module',exports:{'.':'./index.js'},peerDependencies:peers,peerDependenciesMeta:{'@fixtures/absent-peer':{optional}}}));
    const marker=resolve(directory,'imported');
    await writeFile(resolve(directory,'index.js'),`import {writeFileSync} from 'node:fs';writeFileSync(${JSON.stringify(marker)},'imported');export function apply(){}`);
    if(expected){await assert.rejects(createHost({boot:[{specifier}]}),expected);assert.equal(existsSync(marker),false);}
    else {const host=await createHost({boot:[{specifier}]});assert.equal(existsSync(marker),true);await host.close();}
  }
});
