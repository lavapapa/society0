import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {LocalArtifacts} from '../.build/core/dist/artifacts-local/index.js';
import {AttemptLedger} from '../.build/core/dist/attempts/index.js';

// 每个在途模型请求在整段流式期间占一个证据封存槽位；账本证据存储的在途上限须与 maxPending 同量级，否则并发请求被隐式串行（曾把 16 路面板压到约 4 路）。
test('attempts：stageEvidence 的在途上限跟随 maxPending，16 路长流式证据同时开始封存',async()=>{
  const home=await mkdtemp(join(tmpdir(),'attempts-evidence-')),source=await LocalArtifacts.open({directory:join(home,'source')});
  const ledger=await AttemptLedger.open({directory:join(home,'ledger'),lineage:'lineage',maxPending:16},source);
  const gate=Promise.withResolvers();
  try {
    let started=0;
    async function* stream(){started++;await gate.promise;yield Buffer.from('{"delta":1}\n')}
    const works=Array.from({length:16},()=>ledger.stageEvidence(stream(),'application/x-ndjson'));
    // stage 先异步建好封存文件才拉取数据源；限时等待计数稳定，上限过小时停在上限值。
    const deadline=Date.now()+3000;while(started<16&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
    try {assert.equal(started,16)} finally {gate.resolve()}
    assert.equal((await Promise.all(works)).length,16);
  } finally {await ledger.close();await source.close();await rm(home,{recursive:true,force:true})}
});
