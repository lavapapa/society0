import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, table } from './b4-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

// B10 r1：会话快照租约读数据库 maxSnapshotAgeMs；shell 超时先于快照失效，失败类型稳定。
test('B10 快照租约取数据库配置且 shell 超时确定先到', async () => {
    const run = await setup({ maxSnapshotAgeMs: 3000 });
    let session;
    try {
        await run.tx('b10-lease-seed', tx => run.ctx.public.writer(tx).upsert(table.path, [{ id: 'r1', at: '2026-11-01', price: '1.00', note: 'a' }]));
        await run.openMoment('m', 'b10-lease-open');
        const leases = [];
        const original = run.ctx.database.snapshot.bind(run.ctx.database);
        run.ctx.database.snapshot = async (bounds) => { leases.push(bounds.maxAgeMs); return original(bounds); };
        assert.equal(typeof run.ctx.database.maxSnapshotAgeMs, 'number');
        assert.equal(run.ctx.database.maxSnapshotAgeMs, 3000);
        session = await run.ctx.exploration.acquire(run.scope('a', 'm'), run.acquisition('a', 'm', 'b10-lease'));
        await session.value.stat('/public/market/prices/2026-11.jsonl');
        assert.ok(leases.length > 0);
        assert.ok(leases.every(value => value === 3000), `会话快照租约应为 3000，实际 ${JSON.stringify(leases)}`);

        await run.host.ctx.plugin(ExplorationShell, { timeoutMs: 3000 });
        let ctx;
        await run.host.ctx.plugin({ inject: ['explorationShell'], apply(c) { ctx = c; } });
        const started = performance.now();
        await assert.rejects(
            () => ctx.explorationShell.execute('sleep 10; true', session.value, run.acquisition('a', 'm', 'b10-lease-shell')),
            error => {
                assert.doesNotMatch(String(error?.message ?? error), /SQLITE_SNAPSHOT_EXPIRED/u);
                return /SHELL_TIMEOUT/.test(String(error?.code ?? error?.message ?? error));
            }
        );
        const elapsed = performance.now() - started;
        assert.ok(elapsed < 6000, `shell 应在快照失效前结束，实际 ${elapsed} ms`);
    }
    finally {
        try { await session?.release(); } catch {}
        await run.close();
    }
});
