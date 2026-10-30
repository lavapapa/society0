import test from 'node:test';import assert from 'node:assert/strict';import {createRequire} from 'node:module';import {resolve} from 'node:path';import {pathToFileURL,fileURLToPath} from 'node:url';
const stage=fileURLToPath(new URL('../.build',import.meta.url));const require=createRequire(stage+'/package.json');
export const load=async(specifier)=>import(pathToFileURL(require.resolve(specifier)));
for(const name of ['@society0/core/replay','@society0/core/observation','@society0/host/observation-gateway','@society0/host/run-branch'])test('PR11 public assembly '+name,async()=>{const m=await load(name);assert.equal(m.default.Config,m.Config);assert.ok(m.api);assert.ok(m.metadata);});
