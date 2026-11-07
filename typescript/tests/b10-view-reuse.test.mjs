import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, table } from './b4-support.mjs';

const file = '/public/market/prices/2026-11.jsonl';
const row = { id: 'r1', at: '2026-11-01', price: '1.50', note: 'a' };

// B10 r1：同一 moment、同一 revision 内复用会话视图，避免每次工具调用新开快照；
// revision 变化（事务提交）后必须失效重开。
test('B10 会话视图复用：同一 revision 只开一次快照，写后失效', async () => {
    const run = await setup();
    const counts = { opens: 0 };
    const original = run.ctx.database.snapshot.bind(run.ctx.database);
    run.ctx.database.snapshot = async (bounds) => { counts.opens++; return original(bounds); };
    let session;
    try {
        await run.tx('b10-view-seed', tx => run.ctx.public.writer(tx).upsert(table.path, [row]));
        await run.openMoment('m', 'b10-view-open');
        session = await run.ctx.exploration.acquire(run.scope('a', 'm'), run.acquisition('a', 'm', 'b10-view'));
        const baseline = counts.opens;
        for (let index = 0; index < 3; index++) await session.value.stat(file);
        for (let index = 0; index < 2; index++) await session.value.withView(view => view.stat(file));
        assert.equal(counts.opens - baseline, 1, `同一 revision 内应只开一次快照，实际 ${counts.opens - baseline}`);

        const before = counts.opens;
        await run.tx('b10-view-write', tx => run.ctx.public.writer(tx).upsert(table.path, [{ ...row, note: 'b' }]));
        await session.value.stat(file);
        assert.equal(counts.opens - before, 1, 'revision 变化后应重开一次快照');
    }
    finally {
        try { await session?.release(); } catch {}
        await run.close();
    }
});
