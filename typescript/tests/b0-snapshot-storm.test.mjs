import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope } from './pr07-support.mjs';

// B0：17 个并发 stat 在基线上触发 SQLITE_SNAPSHOT_BUDGET（sqlite/database.ts:150）。
// 该用例保持红色，B3a（U4）实现 snapshotQueue 后转绿。
test('B0 并发 stat 风暴不触发 SQLITE_SNAPSHOT_BUDGET', async () => {
    const env = await setup();
    let session;
    try {
        session = await env.ctx.exploration.acquire(scope, env.context('b0-snapshot-storm'));
        const results = await Promise.allSettled(Array.from({ length: 17 }, () => session.value.stat('/workspace')));
        const failures = results.filter(result => result.status === 'rejected').map(result => String(result.reason));
        assert.deepEqual(failures, []);
        assert.equal(results.length, 17);
    }
    finally {
        try { await session?.release(); } catch {}
        await env.close();
    }
});
