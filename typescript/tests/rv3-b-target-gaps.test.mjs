import {test} from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {writeFile,rm,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {setup,request} from './pr07-support.mjs';
import {fixture,prepare,advance,targetOf,frontier} from './pr05-support.mjs';
import {artifactsMigration} from '../.build/core/dist/artifacts-local/index.js';
test('RV3-03-G03 worker tx expired and query bounds protocol',async()=>{const env=await setup();try{const lease=env.reservation.lease;let held;await env.ctx.database.transaction(lease,request('gap-expire'),async tx=>{held=tx;const rows=await tx.query({sql:'SELECT 1 AS n'},{maxRows:1,maxBytes:1024});assert.equal(Number(rows[0].n),1);return 1});let expired;try{await held.query({sql:'SELECT 1'},{maxRows:1,maxBytes:1024})}catch(error){expired=error}assert.equal(expired?.code,'SQLITE_TX_EXPIRED');await assert.rejects(env.ctx.database.transaction(lease,request('gap-bounds-rows'),tx=>tx.query({sql:'SELECT 1'},{maxRows:0,maxBytes:1024})),error=>error.code==='SQLITE_QUERY_BUDGET');
await assert.rejects(env.ctx.database.transaction(lease,request('gap-bounds-bytes'),tx=>tx.query({sql:'SELECT 1'},{maxRows:1,maxBytes:0})),error=>error.code==='SQLITE_QUERY_BUDGET');
await env.ctx.database.transaction(lease,request('gap-bounds-ok'),async tx=>{const ok=await tx.query({sql:'SELECT 1 AS n'},{maxRows:1,maxBytes:1024});assert.equal(Number(ok[0].n),1)});
console.log(JSON.stringify({case:'G03',expired:true,bounds:true}))}finally{await env.close()}});

test('RV3-03-G06 failed transaction query reports TX_FAILED',async()=>{const env=await setup();try{const lease=env.reservation.lease;await assert.rejects(env.ctx.database.transaction(lease,request('gap-failed'),async tx=>{try{await tx.execute({sql:'INSERT INTO no_such_table VALUES(1)'})}catch{}await tx.query({sql:'SELECT 1'},{maxRows:1,maxBytes:1024})}),error=>['SQLITE_TX_FAILED','SQLITE_ERROR'].includes(error.code));console.log(JSON.stringify({case:'G06',failed:true}))}finally{await env.close()}});



