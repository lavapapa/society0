import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope, request } from './pr07-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

// D4：/workspace 与 /results 共用 workspace 后端，但身份、根边界和来源必须区分。
test('D4 /results 身份：版本 owner 按根、禁跨根 rename、写入带 provenance', async () => {
    const env = await setup();
    let session;
    try {
        await env.host.ctx.plugin(ExplorationShell, { timeoutMs: 120000 });
        let ctx;
        await env.host.ctx.plugin({ inject: ['explorationShell', 'exploration', 'database', 'workspace'], apply(c) { ctx = c; } });
        env.ctx.actions.register({
            name: 'd4.write', effect: 'world', inputSchema: {}, outputSchema: {}, available: async () => true,
            async execute(input, c) {
                async function* bytes() { yield Buffer.from(input.text); }
                const node = await ctx.workspace.writeFile(input.path, bytes(), null, scope, c);
                return { operationId: c.operationId, status: 'completed', value: node, original: input, artifacts: [] };
            }
        });
        const context = { ...env.context('d4-results'), transaction: (bounds, body) => ctx.database.transaction(env.reservation.lease, bounds, body) };
        session = await ctx.exploration.acquire(scope, context);
        const write = async (path, text) => (await env.ctx.actions.execute('d4.write', { path, text }, env.context('d4-write-' + path))).value;

        const workspaceCopy = await write('/workspace/same.txt', 'workspace copy');
        const resultsCopy = await write('/results/same.txt', 'results copy');
        assert.notEqual(workspaceCopy.versions[0].owner, resultsCopy.versions[0].owner);
        assert.match(workspaceCopy.versions[0].owner, /^workspace:/);
        assert.match(resultsCopy.versions[0].owner, /^results:/);

        const expected = await session.value.stat('/workspace/same.txt');
        const resultsRoot = await session.value.stat('/results');
        await assert.rejects(
            session.value.mutate({ kind: 'rename', from: '/workspace/same.txt', to: '/results/same.txt', expected, targetExpected: null, expectedParent: resultsRoot }, context),
            error => error.code === 'WORKSPACE_CROSS_ROOT' || String(error).includes('WORKSPACE_CROSS_ROOT')
        );

        async function* rows() { yield { x: 1 }; }
        const saved = await session.value.saveResult('query.jsonl', rows(), context, { query: { prefix: 'a' }, revision: '3', seed: 7 });
        assert.ok(saved.provenance, JSON.stringify(saved));
        assert.equal(saved.provenance.kind, 'save-result');
        assert.deepEqual(saved.provenance.details, { query: { prefix: 'a' }, revision: '3', seed: 7 });

        const result = await ctx.explorationShell.execute('printf manual >/results/manual.txt', session.value, context);
        assert.equal(result.exitCode, 0, result.stderr);
        const manual = await session.value.stat('/results/manual.txt');
        assert.equal(manual.provenance.kind, 'manual');

        // 旧 run 的节点（随机 UUID、无根命名空间）仍可读；本次改动没有 schema 迁移。
        await ctx.database.transaction(env.reservation.lease, request('d4-legacy'), tx => tx.execute({
            sql: 'INSERT INTO workspace_nodes VALUES(?,?,?,?,?,?,?)',
            parameters: ['a', '/results/legacy', '/results', 'legacy-random-uuid', 'directory', 1, null]
        }));
        const legacy = await session.value.stat('/results/legacy');
        assert.equal(legacy.kind, 'directory');
        assert.match(legacy.versions[0].owner, /^results:/);
    }
    finally {
        try { await session?.release(); } catch {}
        await env.close();
    }
});
