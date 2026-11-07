import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, table } from './b4-support.mjs';

const file = '/public/market/prices/2026-11.jsonl';
const row = { id: 'r1', at: '2026-11-01', price: '1.50', note: 'a' };

// B10 返工 2：视图复用只在一次 withView/并发突发内有效；空闲后立即释放快照槽。
test('B10 会话视图复用：withView 内复用，空闲释放，写后失效', async () => {
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
        await session.value.withView(async view => {
            await view.stat(file);
            await view.stat(file);
            await view.stat(file);
        });
        assert.equal(counts.opens - baseline, 1, '一次 withView 内应只开一次快照');
        await new Promise(resolve => setTimeout(resolve, 10));
        assert.equal(run.ctx.database.diagnostics().snapshots, 0, '空闲后应释放快照');

        const idle = counts.opens;
        await session.value.stat(file);
        await session.value.stat(file);
        assert.equal(counts.opens - idle, 2, '两次独立调用之间空闲，应各开一次快照');
        assert.equal(run.ctx.database.diagnostics().snapshots, 0);

        await run.tx('b10-view-write', tx => run.ctx.public.writer(tx).upsert(table.path, [{ ...row, note: 'b' }]));
        const before = counts.opens;
        await session.value.withView(view => view.stat(file));
        assert.equal(counts.opens - before, 1, 'revision 变化后 withView 应重开一次快照');
        assert.equal(run.ctx.database.diagnostics().snapshots, 0, 'withView 结束后应释放快照');
    }
    finally {
        try { await session?.release(); } catch {}
        await run.close();
    }
});
