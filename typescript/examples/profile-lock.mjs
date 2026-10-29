import {readFile,writeFile} from 'node:fs/promises';
const [output,...names]=process.argv.slice(2);
if(!output||!names.length)throw Error('Usage: node profile-lock.mjs <output.json> @society0/host @society0/core [@society0/extras @society0/social]');
const packages={};
for(const name of names){
 const manifest=JSON.parse(await readFile(new URL(import.meta.resolve(name+'/package.json')),'utf8'));
 const key=manifest.society?.plugin?.metadata;
 if(!key)throw Error('STATIC_METADATA_REQUIRED '+name);
 const meta=JSON.parse(await readFile(new URL(import.meta.resolve(name+key.slice(1))),'utf8'));
 packages[name]={version:manifest.version,gitCommit:meta.source.gitCommit,patches:meta.source.patches,hostAbi:meta.source.hostAbi};
}
await writeFile(output,JSON.stringify({lockfile:'installed-public-metadata',packages},null,2)+'\n',{flag:'wx'});
