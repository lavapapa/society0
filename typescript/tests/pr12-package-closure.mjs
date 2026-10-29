import assert from 'node:assert/strict';
import {readdir,readFile,realpath} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {resolve,relative,sep} from 'node:path';
const consumer=resolve('.'),root=resolve('node_modules/@society0'),packages=[];
const local=async path=>{const actual=await realpath(path);assert.ok(!relative(consumer,actual).startsWith('..'+sep),'entry resolves outside installed consumer: '+actual);return actual};
for(const name of await readdir(root)){
 const specifier='@society0/'+name,manifest=JSON.parse(await readFile(resolve(root,name,'package.json'),'utf8'));assert.equal(manifest.name,specifier);assert.ok(manifest.license);
 const entries=[];
 for(const key of Object.keys(manifest.exports??{})){assert.doesNotMatch(key,/workbench|economy|private-skill/i);if(key.includes('*'))continue;entries.push({exportPath:key,path:await local(fileURLToPath(import.meta.resolve(specifier+key.slice(1))))})}
 const executables=[];for(const [command,path] of Object.entries(manifest.bin??{}))executables.push({command,path:await local(resolve(root,name,path))});
 assert.ok(entries.length||executables.length,'declared public entry: '+specifier);packages.push({name:specifier,version:manifest.version,license:manifest.license,entries,executables});
}
console.log(JSON.stringify({consumer,packages,modelRequests:0,scope:'installed package manifests and declared concrete public entry resolution; plugin imports/config/API audited separately'}));
