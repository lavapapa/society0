// cases: D7-5 actors.seed owns s0_actors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, request } from './pr10-support.mjs';

test('D7-5 actors.seed creates actors once from the world actors section', { timeout: 120_000 }, async () => {
    const run = await fixture();
    try {
        const rows = await run.ctx.database.snapshot({ maxAgeMs: 30000 }).then(async view => {
            try { return await view.query({ sql: 'SELECT id,attributes,source FROM s0_actors ORDER BY id' }, { maxRows: 16, maxBytes: 65536 }); }
            finally { await view.close(); }
        });
        assert.deepEqual(rows.map(row => row.id), ['a', 'b', 'c']);
        assert.ok(rows.every(row => row.source === null || typeof row.source === 'string' ? String(row.source).length > 0 : true), JSON.stringify(rows));
        const members = await run.ctx.database.snapshot({ maxAgeMs: 30000 }).then(async view => {
            try { return await view.query({ sql: 'SELECT id FROM social_members ORDER BY id' }, { maxRows: 16, maxBytes: 65536 }); }
            finally { await view.close(); }
        });
        assert.deepEqual(members.map(row => row.id), ['a', 'b', 'c']);
    }
    finally {
        await run.host.close();
    }
});

test('D7-5 a world actor without source is rejected', { timeout: 120_000 }, async () => {
    await assert.rejects(fixture({ worldActors: [{ id: 'a', attributes: { name: '甲' } }, { id: 'b', attributes: { name: '乙' } }, { id: 'c', attributes: { name: '丙' } }] }), /ACTOR_SOURCE_MISSING/);
});

test('D7-5 social membership rejects an actor that actors.seed did not create', { timeout: 120_000 }, async () => {
    const run = await fixture();
    const reservation = await run.ctx.executionFence.reserve({ id: 'd7-missing-actor' });
    try {
        await assert.rejects(
            run.ctx.database.transaction(reservation.lease, request('d7-add-missing'), tx => run.ctx.socialRepository.addMember(tx, { id: 'ghost', attributes: {} })),
            /ACTOR_MISSING/
        );
    }
    finally {
        await reservation.release();
        await run.host.close();
    }
});
