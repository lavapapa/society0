// cases: PR07-T20 PR07-T21 PR07-T24
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, read, scope, page } from './pr07-support.mjs';
function actions(env) {
    const { ctx } = env;
    ctx.actions.register({ name: 'workspace.write', inputSchema: { type: 'object' }, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) {
            const bytes = Buffer.from(input.text);
            async function* source() {
                for (let i = 0; i < bytes.length; i += 4093)
                    yield bytes.subarray(i, i + 4093);
            }
            const node = await ctx.workspace.writeFile(input.path, source(), input.expected ?? null, { ...scope, actorId: context.actorId }, context);
            return { operationId: context.operationId, status: 'completed', value: node, original: input, artifacts: [] };
        } });
    ctx.actions.register({ name: 'workspace.mutate', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) { await ctx.workspace.mutate(input, { ...scope, actorId: context.actorId }, context); return { operationId: context.operationId, status: 'completed', value: null, original: input, artifacts: [] }; } });
}
test('PR07-T20/T24 actor-isolated original range/grep and stale directory mutations', async () => {
    const env = await setup();
    actions(env);
    const { ctx } = env;
    let a, b;
    try {
        a = await ctx.exploration.acquire(scope, env.context('explore'));
        b = await ctx.exploration.acquire({ ...scope, actorId: 'b' }, env.context('explore-b', 'b'));
        const text = 'x'.repeat(65535) + '跨块中文' + 'y'.repeat(70000) + '\n';
        const first = (await ctx.actions.execute('workspace.write', { path: '/workspace/note', text }, env.context('note-a'))).value;
        await ctx.actions.execute('workspace.write', { path: '/workspace/note', text: 'b private' }, env.context('note-b', 'b'));
        assert.notEqual((await b.value.stat('/workspace/note')).objectId, first.objectId);
        await assert.rejects(b.value.open(first), /STALE/);
        const handle = await a.value.open(first);
        try {
            const chunks = [];
            for await (const chunk of handle.value.read({ offset: '65535', length: 12, maxBytes: 12 }, env.reservation.signal))
                chunks.push(chunk);
            assert.equal(Buffer.concat(chunks).toString(), '跨块中文');
        }
        finally {
            await handle.release();
        }
        const hits = await a.value.grep({ root: '/workspace', pattern: '跨块中文', mode: 'literal', page });
        assert.equal(hits.total, '1');
        assert.equal(hits.items[0].rawOffset, '65535');
        assert.equal((await b.value.grep({ root: '/workspace', pattern: '跨块中文', mode: 'literal', page })).total, '0');
        const parent = await a.value.stat('/workspace');
        await ctx.actions.execute('workspace.write', { path: '/workspace/other', text: 'changed' }, env.context('other'));
        await assert.rejects(ctx.actions.execute('workspace.mutate', { kind: 'mkdir', path: '/workspace/dir', expected: null, expectedParent: parent }, env.context('stale-mkdir')), /WORKSPACE_STALE/);
        await ctx.actions.execute('workspace.write', { path: '/workspace/note', text: 'replacement', expected: first }, env.context('replace'));
        await assert.rejects(a.value.open(first), /STALE/);
        await assert.rejects(ctx.actions.execute('workspace.mutate', { kind: 'remove', path: '/workspace/note', expected: first }, env.context('stale-remove')), /WORKSPACE_STALE/);
        const current = await a.value.stat('/workspace/note'), target = await a.value.stat('/workspace/other'), freshParent = await a.value.stat('/workspace');
        await ctx.actions.execute('workspace.write', { path: '/workspace/other', text: 'new target', expected: target }, env.context('replace-target'));
        await assert.rejects(ctx.actions.execute('workspace.mutate', { kind: 'rename', from: '/workspace/note', to: '/workspace/other', expected: current, targetExpected: target, expectedParent: freshParent }, env.context('stale-rename')), /WORKSPACE_STALE/);
    }
    finally {
        await a?.release();
        await b?.release();
        await env.close();
    }
});
test('PR07-T21/T24 world SQL query borrows view, preserves filters/order/projection and saved file', async () => {
    const env = await setup();
    const { ctx } = env;
    let session;
    try {
        session = await ctx.exploration.acquire(scope, env.context('world'));
        const original = await session.value.collect('/world/information/plain.actors', { prefix: 'a', order: 'desc', fields: ['id', 'attributes'] }, page);
        assert.equal(original.total, '1');
        assert.deepEqual(Object.keys(original.items[0].value).sort(), ['attributes', 'id']);
        ctx.actions.register({ name: 'save.query', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) {
                async function* rows() {
                    for (const item of original.items)
                        yield item.value;
                }
                const node = await session.value.saveResult('query.jsonl', rows(), context);
                return { operationId: context.operationId, status: 'completed', value: node, original: { query: { prefix: 'a' }, revision: original.revision }, artifacts: [] };
            } });
        const saved = (await ctx.actions.execute('save.query', null, env.context('save'))).value;
        const handle = await session.value.open(saved);
        try {
            const chunks = [];
            for await (const chunk of handle.value.read({ offset: '0', length: Number(saved.rawBytes), maxBytes: 16384 }, env.reservation.signal))
                chunks.push(chunk);
            assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()), original.items[0].value);
        }
        finally {
            await handle.release();
        }
        let borrowed;
        ctx.information.register({ id: 'borrowed', schema: {}, capabilities: [], available: async (s, v) => { borrowed = v; return true; }, async query(input, s, v) { assert.equal(v, borrowed); assert.equal(ctx.database.diagnostics().snapshots, 1); return { items: [], total: '0', revision: v.revision, responseBytes: 0 }; } });
        const result = await session.value.collect('/world/information/borrowed', {}, page);
        assert.equal(result.total, '0');
    }
    finally {
        await session?.release();
        await env.close();
    }
});
