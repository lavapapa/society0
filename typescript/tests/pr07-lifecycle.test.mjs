import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope, page } from './pr07-support.mjs';
async function* body(text) { yield Buffer.from(text); }
test('PR07 nested keyset traversal, subtree rename, configured budgets and retained original pin', async () => {
    const env = await setup({ explorationOptions: { maxSearchBytes: 1024, maxInlineFileBytes: 128 } }), { ctx } = env;
    let session, pin;
    try {
        session = await ctx.exploration.acquire(scope, env.context('filesystem-session'));
        let work;
        ctx.actions.register({ name: 'filesystem', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) { const value = await work(context); return { operationId: context.operationId, status: 'completed', value: value ?? null, original: { operationId: context.operationId }, artifacts: [] }; } });
        async function change(id, body) { work = body; return ctx.actions.execute('filesystem', null, env.context(id)); }
        const roots = await session.value.list('/', { ...page, maxItems: 10 });
        assert.deepEqual(roots.items.map(node => node.path), ['/results', '/workspace', '/world']);
        await change('mkdir', async (context) => session.value.mutate({ kind: 'mkdir', path: '/workspace/a', expectedParent: await session.value.stat('/workspace') }, context));
        await change('nested', context => session.value.writeFile('/workspace/a/z', body('完整中文'), null, context));
        await change('sibling', context => session.value.writeFile('/workspace/a.txt', body('x'.repeat(2048)), null, context));
        let cursor;
        const paths = [];
        do {
            const result = await session.value.find({ root: '/workspace', glob: '*', page: { ...page, maxItems: 1, cursor } });
            paths.push(...result.items.map(node => node.path));
            cursor = result.next;
        } while (cursor);
        assert.deepEqual(paths, ['/workspace', '/workspace/a', '/workspace/a/z', '/workspace/a.txt']);
        await assert.rejects(session.value.grep({ root: '/workspace', pattern: '中文', mode: 'literal', page }), /EXPLORATION_SEARCH_BUDGET/);
        assert.equal(ctx.database.diagnostics().snapshots, 0);
        await assert.rejects(change('row-budget', async (context) => { async function* rows() { yield { large: 'r'.repeat(256) }; } return session.value.saveResult('large.jsonl', rows(), context); }), /EXPLORATION_ROW_BUDGET/);
        const old = await session.value.stat('/workspace/a/z');
        pin = await session.value.open(old);
        await change('rename-tree', async (context) => session.value.mutate({ kind: 'rename', from: '/workspace/a', to: '/workspace/b', expected: await session.value.stat('/workspace/a'), expectedParent: await session.value.stat('/workspace'), targetExpected: null }, context));
        assert.equal((await session.value.stat('/workspace/b/z')).objectId, old.objectId);
        await assert.rejects(session.value.open(old), /EXPLORATION_STALE/);
        let text = '';
        for await (const chunk of pin.value.read({ offset: '0', length: Number(old.rawBytes), maxBytes: 1024 }, env.reservation.signal))
            text += Buffer.from(chunk).toString();
        assert.equal(text, '完整中文');
        let released = false;
        const closing = session.release().then(() => { released = true; });
        await Promise.resolve();
        assert.equal(released, false);
        await pin.release();
        pin = undefined;
        await closing;
        assert.equal(released, true);
    }
    finally {
        await pin?.release();
        await session?.release();
        await env.close();
    }
});
