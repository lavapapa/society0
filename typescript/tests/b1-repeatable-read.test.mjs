import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope } from './pr07-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

// B1：一个 bash 命令（= 一次工具调用）内只持有一个读视图；期间提交的写不改变该视图的 revision。
test('B1 单个 bash 命令内两次 find 看到同一 revision', async () => {
    const env = await setup();
    let session;
    try {
        await env.host.ctx.plugin(ExplorationShell, { timeoutMs: 120000 });
        let ctx;
        await env.host.ctx.plugin({ inject: ['explorationShell', 'exploration', 'database', 'workspace'], apply(c) { ctx = c; } });
        const context = { ...env.context('b1-repeatable'), transaction: (request, body) => ctx.database.transaction(env.reservation.lease, request, body) };
        env.ctx.actions.register({
            name: 'probe.write', effect: 'world', inputSchema: {}, outputSchema: {}, available: async () => true,
            async execute(input, c) {
                async function* bytes() { yield Buffer.from('marker'); }
                const node = await ctx.workspace.writeFile('/workspace/marker', bytes(), null, scope, c);
                return { operationId: c.operationId, status: 'completed', value: node, original: input, artifacts: [] };
            }
        });
        const revisions = [];
        let written = false;
        const file = { path: '/probe/x.jsonl', objectId: 'probe-x', kind: 'file', versions: [{ owner: 'probe', version: '1' }], rawBytes: '6' };
        ctx.exploration.register({
            id: 'probe/repeatable', prefix: '/probe',
            visible: async path => path === '/probe' || path === '/probe/x.jsonl',
            stat: async (path, _scope, view) => {
                revisions.push(view.revision);
                return path === '/probe'
                    ? { path: '/probe', objectId: 'probe-root', kind: 'directory', versions: [{ owner: 'probe', version: view.revision }] }
                    : file;
            },
            list: async (_path, _scope, view, request) => {
                revisions.push(view.revision);
                if (!written) { written = true; await env.ctx.actions.execute('probe.write', null, env.context('probe-write')); }
                return { items: [file], total: '1', revision: view.revision, responseBytes: 0, ...({}) };
            },
            open: async () => { throw Error('UNUSED'); },
            collect: async () => { throw Error('UNUSED'); }
        });
        session = await ctx.exploration.acquire(scope, context);
        const result = await ctx.explorationShell.execute("find /probe '*'; find /probe '*'", session.value, context);
        assert.equal(result.exitCode, 0, result.stderr);
        assert.ok(written);
        assert.equal(revisions.length >= 4, true, JSON.stringify(revisions));
        assert.equal(new Set(revisions).size, 1, `同一命令内出现了多个 revision: ${JSON.stringify(revisions)}`);
        const current = await ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            assert.ok(BigInt(current.revision) > BigInt(revisions[0]), `写事务应推进数据库 revision: ${revisions[0]} -> ${current.revision}`);
        }
        finally { await current.close(); }
    }
    finally {
        try { await session?.release(); } catch {}
        await env.close();
    }
});
