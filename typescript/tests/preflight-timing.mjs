import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const base=resolve(process.argv[2]);
const {preflight}=await import(pathToFileURL(resolve(base,'node_modules/@society0/host/dist/packages.js')));
for(const modules of [[],['@society0/core/sqlite']]){
  const start=performance.now();
  preflight(modules,pathToFileURL(resolve(base,'package.json')).href);
  console.log(JSON.stringify({modules,milliseconds:performance.now()-start}));
}
