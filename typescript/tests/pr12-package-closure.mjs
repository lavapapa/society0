import assert from 'node:assert/strict';
import {readdir,readFile,realpath} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,relative,sep} from 'node:path';
const consumer=resolve('.'),root=resolve('node_modules/@society0'),packages=[];
for(const name of await readdir(root)){
 const specifier='@society0/'+name;
 const path=fileURLToPath(import.meta.resolve(specifier));
 const actual=await realpath(path);
 assert.ok(!relative(consumer,actual).startsWith('..'+sep),'package resolves outside installed consumer: '+specifier);
 let manifest;
 try{manifest=JSON.parse(await readFile(new URL(import.meta.resolve(specifier+'/package.json')),'utf8'));}
 catch(error){if(error.code!=='ERR_PACKAGE_PATH_NOT_EXPORTED')throw error;const namespace=await import(specifier);packages.push({name:specifier,rootExports:Object.keys(namespace),resolvedEntry:actual,publicManifest:false});continue;}
 assert.equal(manifest.name,specifier);assert.ok(manifest.license);
 for(const key of Object.keys(manifest.exports))assert.doesNotMatch(key,/workbench|economy|private-skill/i);
 const namespace=await import(specifier);
 packages.push({name:specifier,version:manifest.version,license:manifest.license,resolvedEntry:actual,rootExports:Object.keys(namespace),declaredExports:Object.keys(manifest.exports)});
}
console.log(JSON.stringify({consumer,packages,modelRequests:0,scope:'installed society0 package roots, resolution and declared exports; plugin catalog audited separately'}));
