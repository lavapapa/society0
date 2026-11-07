import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './b4-support.mjs';

const decl = {
    path: '/public/slots', title: '槽位', description: '',
    schema: { type: 'object', properties: { id: { type: 'string' }, note: { type: 'string' } } },
    key: ['id'], sort: [{ field: 'id' }], shard: { by: '', format: 'none' }
};
const file = '/public/slots/slots.jsonl';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// B10 返工 2：会话空闲后不得持有全局快照槽；12 个会话共享 4 个槽也不得长时间排队。
test('B10 会话空闲释放快照槽，12 会话共用 4 个槽不排队', async () => {
    const run = await setup({ tables: [decl], maxSnapshots: 4, maxSnapshotAgeMs: 30000 });
    const sessions = [];
    try {
        await run.tx('b10-slots-seed', tx => run.ctx.public.writer(tx).upsert(decl.path, [{ id: 'r1', note: 'a' }]));
        await run.openMoment('m', 'b10-slots-open');
        for (let index = 0; index < 12; index++)
            sessions.push(await run.ctx.exploration.acquire(run.scope('a', 'm'), run.acquisition('a', 'm', 'b10-slots-' + index)));
        const waits = [];
        for (let round = 0; round < 3; round++) {
            await Promise.all(sessions.map(async (session, index) => {
                const started = performance.now();
                await session.value.withView(async view => { await view.stat(file); });
                waits.push(performance.now() - started);
                if ((index + round) % 3 === 0) await sleep(10);
            }));
            await sleep(20);
            assert.equal(run.ctx.database.diagnostics().snapshots, 0, `第 ${round} 轮空闲后不应持有快照`);
        }
        assert.equal(waits.filter(value => value > 1000).length, 0, `读取等待超过 1s: ${JSON.stringify(waits)}`);
        await sleep(20);
        assert.equal(run.ctx.database.diagnostics().snapshots, 0);
        assert.equal(run.ctx.database.diagnostics().snapshotQueue, 0);
    }
    finally {
        for (const session of sessions) await session.release();
        await run.close();
    }
});

// B10 返工 2：并发读取期间 revision 变化，空闲后仍释放视图且不报错。
test('B10 并发读取与外部 revision 变化不破坏在用视图', async () => {
    const run = await setup({ tables: [decl], maxSnapshots: 4, maxSnapshotAgeMs: 30000 });
    let session;
    try {
        await run.tx('b10-race-seed', tx => run.ctx.public.writer(tx).upsert(decl.path, [{ id: 'r1', note: 'a' }]));
        await run.openMoment('m', 'b10-race-open');
        session = await run.ctx.exploration.acquire(run.scope('a', 'm'), run.acquisition('a', 'm', 'b10-race'));
        const reads = Array.from({ length: 16 }, (_, index) => session.value.withView(async view => {
            await view.stat(file);
            await sleep(index % 4);
            await view.stat(file);
        }));
        const write = (async () => {
            await sleep(1);
            await run.tx('b10-race-write', tx => run.ctx.public.writer(tx).upsert(decl.path, [{ id: 'r1', note: 'b' }]));
            await session.value.stat(file);
        })();
        const results = await Promise.allSettled([...reads, write]);
        assert.deepEqual(results.filter(result => result.status === 'rejected').map(result => String(result.reason)), []);
        await sleep(20);
        assert.equal(run.ctx.database.diagnostics().snapshots, 0, '空闲后应释放视图');
    }
    finally {
        try { await session?.release(); } catch {}
        await run.close();
    }
});
