import {test} from 'node:test';
import assert from 'node:assert/strict';
const entries=['actors','drivers','actor-eligibility','activation','activation-mailbox','clock-ticks','clock-event','proposal-sequence','proposal-events','runtime-staged','runtime-events','completion-strict','checkpoint-policy','termination','generation-views','generation-binding','run-controller'];
for(const name of entries)test('PR06 assembly '+name,async()=>{
 const module=await import('../.build/core/dist/'+name+'/index.js');
 assert.equal(module.default.Config,module.Config);assert.equal(typeof module.Config,'function');assert.equal(module.metadata.hostAbi,'society0/host/1');assert.equal(module.metadata.configVersion,1);assert.equal(module.api.exportPath,'./'+name);
 for(const service of [...module.api.provides,...module.api.requires.map(x=>x.service)]){assert.equal(service.protocol,service.key);assert.equal(service.major,1);assert.deepEqual(service.capabilities,[])}
 assert.deepEqual([...module.default.inject??[]].sort(),module.api.requires.filter(x=>!x.optional).map(x=>x.service.key).sort());
});
