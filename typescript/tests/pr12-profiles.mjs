import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
for (const name of ['rule-sqlite','event-sqlite','social-sqlite','social-codex-sqlite']) test(name+' literal profile', async()=>{
 const p=JSON.parse(await readFile(new URL('../profiles/'+name+'.json',import.meta.url),'utf8'));
 assert.equal(p.format,'society-profile/v2');
 const flatten=entries=>entries.flatMap(e=>e.group?flatten(e.config):[e]);
 const entries=flatten(p.patches[0].insert);
 assert.ok(entries.some(e=>e.name==='@society0/core/sqlite'));
 assert.ok(entries.some(e=>e.name==='@society0/core/observation'));
 assert.equal(p.patches[0].insert[0].lifetime,'run');
});
