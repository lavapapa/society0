import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, read, request, page, scope } from './pr07-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import ruleDriver, { ruleHandlerSlot } from '../.build/core/dist/driver-rule/index.js';
import passive from '../.build/core/dist/rule-passive/index.js';
import actorPerception from '../.build/core/dist/perception-actor/index.js';
test('PR07-T22 two sources commit input/raw links/actor and thread cursors atomically', async () => {
    const env = await setup();
    const { ctx } = env;
    let lease, failSecond = true, borrowed;
    try {
        for (const [id, cursorScope] of [['actor-stream', 'actor'], ['thread-stream', 'thread']])
            ctx.perceptions.register({ id, schema: {}, cursorScope, available: async () => true, versions: async (scope, reader) => {
                    if (failSecond && id === 'thread-stream' && reader.baseRevision !== undefined)
                        throw Error('second source commit failed');
                    const [row] = await reader.query({ sql: 'SELECT quantity FROM inventory WHERE id=?', parameters: ['stock'] }, { maxRows: 1, maxBytes: 1024 });
                    return [{ owner: 'inventory', version: String(row.quantity) }];
                }, prepare: async () => { assert.equal(ctx.database.diagnostics().snapshots, 0); return { material: { kind: 'inline', value: { prepared: true } }, versions: [{ owner: 'inventory', version: '1' }] }; }, collect: async (scope, after, through, reader, bounds, prepared) => {
                    assert.equal(prepared.material.value.prepared, true);
                    if (id === 'actor-stream')
                        borrowed = reader;
                    else
                        assert.equal(reader, borrowed);
                    return { items: after === 1 ? [] : [{ id: id + '-1', kind: 'notice', material: { kind: 'inline', value: { source: id, raw: '完整原文' } }, provenance: { provider: 'third-party' } }], nextCursor: 1, through: 1, hasMore: false };
                } });
        lease = await ctx.perceptions.acquire(['actor-stream', 'thread-stream'], env.context('perception'));
        const thread = await read(ctx, v => ctx.threads.open('a', 'm', v));
        const ps = { actorId: 'a', momentId: 'm', threadId: thread.id, reason: {} };
        const batch = await lease.value.collect(ps, page, env.context('collect'));
        const inputs = [];
        for (const source of batch.reads)
            inputs.push({ sourceId: source.sourceId, perceptIds: source.page.items.map(x => x.id), message: await ctx.threads.prepare({ id: source.sourceId, interactionId: thread.interactionId, role: 'user', content: source.page.items, originalProviderFields: { source: source.sourceId } }), contexts: source.page.items.map(x => x.material) });
        await assert.rejects(ctx.database.transaction(env.reservation.lease, request('perception-fail'), tx => lease.value.commit(tx, batch, inputs)), /second source commit failed/);
        assert.equal((await read(ctx, v => v.query({ sql: 'SELECT count(*) n FROM perception_progress' }, { maxRows: 1, maxBytes: 1024 })))[0].n, 0n);
        assert.equal((await read(ctx, v => ctx.threads.page(thread.id, scope, v, page))).total, '0');
        failSecond = false;
        await ctx.database.transaction(env.reservation.lease, request('perception-ok'), tx => lease.value.commit(tx, batch, inputs));
        assert.equal((await read(ctx, v => ctx.threads.page(thread.id, scope, v, page))).total, '2');
        assert.equal((await read(ctx, v => v.query({ sql: 'SELECT count(*) n FROM perception_links' }, { maxRows: 1, maxBytes: 1024 })))[0].n, 2n);
        const same = await lease.value.collect(ps, page, env.context('same'));
        assert.deepEqual(same.reads.map(r => r.page.items.length), [0, 0]);
        const nextThread = await read(ctx, v => ctx.threads.open('a', 'next', v));
        const next = await lease.value.collect({ ...ps, momentId: 'next', threadId: nextThread.id }, page, env.context('next', 'a', 'next'));
        assert.deepEqual(next.reads.map(r => r.page.items.length), [0, 1]);
        await assert.rejects(ctx.database.transaction(env.reservation.lease, request('duplicate-batch'), tx => lease.value.commit(tx, batch, inputs)), /PERCEPTION_STALE/);
    }
    finally {
        await lease?.release();
        await env.close();
    }
});
test('rule driver consumes stored structured perception and returns real required action operations', async () => {
    const env = await setup();
    let selection;
    try {
        await env.host.ctx.plugin(Drivers);
        await env.host.ctx.plugin(actorPerception);
        await env.host.ctx.plugin(ruleDriver, { handler: 'rule/test', sources: ['actor/v1'] });
        let ctx;
        await env.host.ctx.plugin({ inject: ['slots', 'drivers', 'actions', 'threads', 'database'], apply(c) { ctx = c; c.slots.contribute(ruleHandlerSlot, { id: 'rule/test', value: { async activate(input, context) { const messages = await read(c, v => c.threads.page(input.threadId, { actorId: context.actorId, momentId: context.momentId, query: null }, v, page)); assert.ok(Number(messages.total) > 0); const receipt = await c.actions.execute('plain.update', { attributes: { rule: true } }, context); return { status: 'completed', value: receipt.value, evidence: { receipt }, requiredOperations: [receipt.operationId] }; } } }); } });
        selection = await ctx.drivers.acquire('rule', env.context('rule'));
        const result = await selection.value.activate({ id: 'intent', actorId: 'a', momentId: 'm', source: 'test', driver: 'rule', input: { task: 'update' } }, env.context('rule-action'));
        assert.equal(result.status, 'completed');
        assert.equal(result.requiredOperations.length, 2);
        for (const id of result.requiredOperations)
            assert.equal((await ctx.database.probe(id)).status, 'committed');
        assert.deepEqual((await read(ctx, v => env.ctx.actors.get('a', v))).attributes, { rule: true });
    }
    finally {
        await selection?.release();
        await env.close();
    }
});
