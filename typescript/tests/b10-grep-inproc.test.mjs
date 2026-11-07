import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './b4-support.mjs';
import { searchProcesses } from '../.build/core/dist/exploration/search.js';

const decl = {
    path: '/public/small', title: '小结果', description: '',
    schema: { type: 'object', properties: { id: { type: 'string' }, file: { type: 'string' }, note: { type: 'string' } } },
    key: ['id'], sort: [{ field: 'file' }, { field: 'id' }], shard: { by: 'file', format: 'value' }
};

// B10 r1：小候选集的字面量 grep 走进程内匹配，不再为每次调用 spawn rg。
test('B10 小候选集字面量 grep 不 spawn rg', async () => {
    const run = await setup({ tables: [decl] });
    try {
        const rows = Array.from({ length: 50 }, (_, index) => ({ id: 'r' + index, file: 'f' + index, note: index % 5 === 0 ? 'needle' : 'other' }));
        await run.tx('b10-small-seed', tx => run.ctx.public.writer(tx).upsert(decl.path, rows));
        await run.openMoment('m', 'b10-small-open');
        const session = await run.ctx.exploration.acquire(run.scope('a', 'm'), run.acquisition('a', 'm', 'b10-small'));
        try {
            const before = searchProcesses.started;
            const page = await session.value.grep({ root: decl.path, pattern: 'needle', mode: 'literal', page: { maxItems: 512, maxResponseBytes: 1048576 } });
            const spawned = searchProcesses.started - before;
            assert.equal(spawned, 0, `小候选集仍 spawn 了 ${spawned} 个 rg`);
            assert.equal(page.items.length, 10);
            assert.ok(page.items.every(item => item.path.startsWith(decl.path + '/')));
        }
        finally { await session.release(); }
    }
    finally {
        await run.close();
    }
});
