import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Information } from '../.build/core/dist/information/index.js';
import { Actions } from '../.build/core/dist/actions/index.js';
test('PR07 production entrypoints exist', () => { assert.equal(typeof Information, 'function'); assert.equal(typeof Actions, 'function'); });
import { setup, read, request, scope, page } from './pr07-support.mjs';
test('PR07-T01 third-party action rolls back actors and durable receipts with one native tx', async () => {
    const env = await setup();
    const { ctx } = env;
    try {
        ctx.actions.register({ name: 'notes.fail', inputSchema: { type: 'object', required: ['body'], properties: { body: { type: 'string' } }, additionalProperties: false }, outputSchema: {}, effect: 'world', available: async () => true, execute: async (input, context) => { await context.transaction(request(context.operationId), async (tx) => { await ctx.actors.update(tx, { id: 'a', attributes: { changed: true } }); await tx.execute({ sql: 'UPDATE inventory SET quantity=2 WHERE id=?', parameters: ['stock'] }); throw Error('notes required receipt failed'); }); throw Error('unreachable'); } });
        await assert.rejects(ctx.actions.execute('notes.fail', { body: 'note' }, env.context('failed')), /required receipt failed/);
        assert.deepEqual(await read(ctx, v => ctx.actors.get('a', v)), { id: 'a', attributes: { score: 0, raw: { preserved: true } } });
        assert.equal((await read(ctx, v => v.query({ sql: 'SELECT count(*) AS n FROM action_receipts' }, { maxRows: 1, maxBytes: 1024 })))[0].n, 0n);
        await assert.rejects(ctx.actions.execute('notes.fail', { body: 123 }, env.context('invalid')), /ACTION_INPUT/);
        const receipt = await ctx.actions.execute('plain.update', { attributes: { changed: true, raw: { vendor: 1 } } }, env.context('valid'));
        assert.equal(receipt.status, 'completed');
        assert.equal((await read(ctx, v => v.query({ sql: 'SELECT value FROM action_receipts WHERE operation_id=?', parameters: ['valid'] }, { maxRows: 1, maxBytes: 4096 })))[0].value, JSON.stringify({ id: 'a', attributes: { changed: true, raw: { vendor: 1 } } }));
    }
    finally {
        await env.close();
    }
});
test('PR07-T05/T23 real attempt survives expired revision and inventory eligibility after external wait', async () => {
    const env = await setup();
    const { ctx, reservation } = env;
    try {
        ctx.actorEligibility.register({ id: 'stock', async evaluate(request, reader) { const [row] = await reader.query({ sql: 'SELECT quantity FROM inventory WHERE id=?', parameters: ['stock'] }, { maxRows: 1, maxBytes: 1024 }); return { allowed: request.kind !== 'action' || request.action !== 'external.purchase' || row.quantity > 0, reason: 'stock', facts: { quantity: Number(row.quantity) }, versions: [{ owner: 'inventory', version: String(row.quantity) }] }; } });
        const started = Promise.withResolvers(), finished = Promise.withResolvers();
        const attempt = { lineage: 'pr07', operationId: 'physical-order', attempt: 0 };
        let expectedRevision = await ctx.database.readRevision();
        ctx.actions.register({ name: 'external.purchase', inputSchema: { type: 'object' }, outputSchema: {}, effect: 'external', available: async () => true, execute: async (input, context) => { assert.equal((await ctx.attempts.claim(attempt, { input })).status, 'send-permitted'); started.resolve(); const original = await finished.promise; await ctx.attempts.settle(attempt, original); await context.transaction({ ...request('purchase-commit'), expectedRevision }, async (tx) => { await ctx.actors.update(tx, { id: 'a', attributes: { purchased: true } }); return original; }); return { operationId: 'purchase-commit', status: 'completed', value: original, original, artifacts: [] }; } });
        const work = ctx.actions.execute('external.purchase', {}, env.context('purchase'));
        void work.catch(() => { });
        await started.promise;
        assert.equal(ctx.database.diagnostics().snapshots, 0);
        await ctx.database.transaction(reservation.lease, request('stock-expires'), tx => tx.execute({ sql: 'UPDATE inventory SET quantity=0' }));
        finished.resolve({ provider: 'local-physical', receipt: 'raw-1' });
        await assert.rejects(work, /SQLITE_STALE_REVISION/);
        assert.equal((await ctx.attempts.probe(attempt)).record.status, 'settled');
        expectedRevision = undefined;
        const again = { ...attempt, operationId: 'late-exit' };
        ctx.actions.register({ name: 'external.late', inputSchema: {}, outputSchema: {}, effect: 'external', available: async () => true, execute: async (input, context) => { await ctx.attempts.claim(again, { input }); await ctx.attempts.settle(again, { raw: 'late' }); await ctx.database.transaction(reservation.lease, request('exit-actor'), tx => ctx.actors.setParticipation(tx, 'a', 'inactive', { reason: 'exit' })); await context.transaction(request('late-write'), tx => ctx.actors.update(tx, { id: 'a', attributes: { late: true } })); return { operationId: 'late-write', status: 'completed', value: null, original: null, artifacts: [] }; } });
        await assert.rejects(ctx.actions.execute('external.late', null, env.context('late')), /ACTION_INELIGIBLE/);
        assert.equal((await ctx.attempts.probe(again)).record.status, 'settled');
        const discovered = await read(ctx, v => ctx.actions.discover(scope, v, page));
        assert.equal(discovered.total, '0');
        assert.deepEqual((await read(ctx, v => ctx.actors.get('a', v))).attributes, { score: 0, raw: { preserved: true } });
    }
    finally {
        await env.close();
    }
});
test('PR07-T23 inventory expiry without expectedRevision is denied inside final action transaction', async () => {
    const env = await setup();
    const { ctx } = env;
    try {
        ctx.actorEligibility.register({ id: 'inventory-final', async evaluate(request, reader) { const [row] = await reader.query({ sql: 'SELECT quantity FROM inventory WHERE id=?', parameters: ['stock'] }, { maxRows: 1, maxBytes: 1024 }); return { allowed: request.kind !== 'action' || request.action !== 'purchase.final' || row.quantity > 0n, reason: 'inventory', facts: { quantity: Number(row.quantity) }, versions: [{ owner: 'inventory', version: String(row.quantity) }] }; } });
        const started = Promise.withResolvers(), finished = Promise.withResolvers();
        ctx.actions.register({ name: 'purchase.final', inputSchema: {}, outputSchema: {}, effect: 'external', available: async () => true, async execute(input, context) { started.resolve(); await finished.promise; await context.transaction(request('inventory-final-write'), async (tx) => { await ctx.actors.update(tx, { id: 'a', attributes: { purchased: true } }); return { physical: 'original' }; }); return { operationId: context.operationId, status: 'completed', value: null, original: { physical: 'original' }, artifacts: [] }; } });
        const work = ctx.actions.execute('purchase.final', null, env.context('inventory-final'));
        void work.catch(() => { });
        await started.promise;
        assert.equal(ctx.database.diagnostics().snapshots, 0);
        await ctx.database.transaction(env.reservation.lease, request('inventory-empty'), tx => tx.execute({ sql: 'UPDATE inventory SET quantity=0' }));
        finished.resolve();
        await assert.rejects(work, /ACTION_INELIGIBLE/);
        assert.equal((await read(ctx, v => ctx.actors.get('a', v))).attributes.score, 0);
        assert.equal((await read(ctx, v => v.query({ sql: 'SELECT count(*) AS n FROM action_receipts WHERE operation_id=?', parameters: ['inventory-final-write'] }, { maxRows: 1, maxBytes: 1024 })))[0].n, 0n);
        assert.equal((await ctx.attempts.probe({ lineage: 'pr07', operationId: 'action:inventory-final', attempt: 0 })).record.status, 'unknown');
    }
    finally {
        await env.close();
    }
});
