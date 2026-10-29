import assert from 'node:assert/strict';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';
const consumer=resolve(process.argv[2]);const {forkProfile}=await import(pathToFileURL(join(consumer,'run-profile-branch.mjs')).href);
await assert.rejects(forkProfile({targetProfile:join(consumer,'profiles/event-sqlite.json'),targetDirectory:join(consumer,'must-not-create'),targetRunId:'contract',advanceTarget:true}),/TARGET_EVENTS_REQUIRED/);
console.log(JSON.stringify({missingBatch:'rejected before opening source or creating target'}));
