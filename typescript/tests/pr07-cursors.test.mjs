import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, read, request, scope, page } from './pr07-support.mjs';
import Random from '../../../evidence/pr07/stage/core/dist/random/index.js';
test('PR07-T06 deep discovery keeps bounded cursors and separately counts eligibility scans', async () => {
    const env = await setup();
    const { ctx } = env;
    try {
        let remove;
        for (let i = 0; i < 250; i++) {
            const id = 'item/' + String(i).padStart(3, '0');
            ctx.actions.register({ name: id, inputSchema: { type: 'object', 'x-provider': { raw: i } }, outputSchema: {}, effect: 'read', available: async () => i % 2 === 0, execute: async () => ({ operationId: id, status: 'completed', value: i, original: { raw: i }, artifacts: [] }) });
            const close = ctx.information.register({ id, schema: { raw: i }, capabilities: ['raw'], available: async (s) => s.actorId === 'a', query: async (input, s, v) => ({ items: [], total: '0', revision: v.revision, responseBytes: 0 }) });
            if (i === 0)
                remove = close;
        }
        const first = await read(ctx, v => ctx.actions.discover(scope, v, { ...page, maxItems: 3 }));
        assert.equal(first.total, '126');
        assert.ok(Buffer.byteLength(first.next) < 1500);
        let cursor = first.next, count = first.items.length, previous = first.items.at(-1).name;
        while (cursor) {
            const result = await read(ctx, v => ctx.actions.discover(scope, v, { ...page, maxItems: 17, cursor }));
            for (const item of result.items) {
                assert.ok(item.name > previous);
                previous = item.name;
            }
            count += result.items.length;
            cursor = result.next;
        }
        assert.equal(count, 126);
        assert.ok(ctx.actions.metrics.eligibilityScans > ctx.actions.metrics.keysetCandidates);
        const discovered = await read(ctx, v => ctx.information.discover(scope, v, { ...page, maxItems: 1 }));
        assert.equal(discovered.total, '251');
        await remove();
        ctx.information.register({ id: 'item/000', schema: { replacement: true }, capabilities: [], available: async () => true, query: async (i, s, v) => ({ items: [], total: '0', revision: v.revision, responseBytes: 0 }) });
        await assert.rejects(read(ctx, v => ctx.information.discover(scope, v, { ...page, cursor: discovered.next })), /CURSOR_STALE/);
        await ctx.database.transaction(env.reservation.lease, request('inactive'), tx => ctx.actors.setParticipation(tx, 'a', 'inactive', { why: 'test' }));
        await assert.rejects(read(ctx, v => ctx.actions.discover(scope, v, { ...page, cursor: first.next })), /CURSOR_STALE/);
        assert.equal((await read(ctx, v => ctx.actions.discover(scope, v, page))).total, '0');
    }
    finally {
        await env.close();
    }
});
test('PR07-T21 actual public Philox supplies deterministic ranks; SQL filters/projects/pages selected actors', async () => {
    const env = await setup();
    const { ctx } = env;
    let session;
    try {
        await env.host.ctx.plugin(Random, { seed: 'pr07-study' });
        await ctx.database.transaction(env.reservation.lease, request('sample-actors'), async (tx) => {
            for (let i = 0; i < 30; i++)
                await ctx.actors.create(tx, { id: 'p' + String(i).padStart(2, '0'), attributes: { score: i, extra: { original: true } } });
        });
        const query = { prefix: 'p', order: 'asc', fields: ['id', 'attributes'], sample: { size: 7, seed: 'trial-9' } };
        async function collect(maxItems) {
            const all = [];
            let cursor;
            do {
                const result = await read(ctx, v => ctx.information.query('plain.actors', query, scope, v, { ...page, maxItems, cursor }));
                assert.equal(result.total, '7');
                all.push(...result.items);
                cursor = result.next;
            } while (cursor);
            return all;
        }
        const first = await collect(2), second = await collect(4);
        assert.deepEqual(first, second);
        const projectedQuery = { ...query, fields: ['attributes'] };
        const projection = await read(ctx, v => ctx.information.query('plain.actors', projectedQuery, scope, v, { ...page, maxItems: 2 }));
        assert.deepEqual(Object.keys(projection.items[0].value), ['attributes']);
        const projectionNext = await read(ctx, v => ctx.information.query('plain.actors', projectedQuery, scope, v, { ...page, maxItems: 2, cursor: projection.next }));
        assert.deepEqual(projectionNext.items.map(item => item.value.attributes), first.slice(2, 4).map(item => item.value.attributes));
        assert.equal(new Set(first.map(item => item.value.id)).size, 7);
        assert.ok(first.every(item => item.value.id.startsWith('p') && item.value.attributes.extra.original));
        session = await ctx.exploration.acquire(scope, env.context('sample-save'));
        ctx.actions.register({ name: 'sample.save', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) {
                async function* rows() {
                    for (const item of first)
                        yield item.value;
                }
                const node = await session.value.saveResult('sample.jsonl', rows(), context, { path: '/world/information/plain.actors', query, sourceRevision: await ctx.database.readRevision(), algorithm: 'philox4x32-10/v1' });
                return { operationId: context.operationId, status: 'completed', value: node, original: { query }, artifacts: [] };
            } });
        const node = (await ctx.actions.execute('sample.save', null, env.context('sample-save-action'))).value;
        assert.equal(node.provenance.source.query.sample.seed, 'trial-9');
        const pin = await session.value.open(node);
        try {
            let raw = '';
            for await (const bytes of pin.value.read({ offset: '0', length: Number(node.rawBytes), maxBytes: 16384 }, env.reservation.signal))
                raw += Buffer.from(bytes).toString();
            assert.deepEqual(raw.trim().split('\n').map(line => JSON.parse(line)), first.map(item => item.value));
        }
        finally {
            await pin.release();
        }
    }
    finally {
        await session?.release();
        await env.close();
    }
});
