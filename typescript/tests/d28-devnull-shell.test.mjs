import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope } from './pr07-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

const version = [{ owner: 'd28-self', version: '1' }];
const files = {
    '/self/actions/offers.jsonl': '{"id":"offer-1"}\n',
    '/self/econ/agreements.jsonl': '{"id":"agreement-1"}\n'
};
const nodes = new Map([['/self', { path: '/self', objectId: 'self', kind: 'directory', versions: version }], ['/self/actions', { path: '/self/actions', objectId: 'actions', kind: 'directory', versions: version }], ['/self/econ', { path: '/self/econ', objectId: 'econ', kind: 'directory', versions: version }]]);
for (const path of Object.keys(files))
    nodes.set(path, { path, objectId: path, kind: 'file', versions: version, rawBytes: String(Buffer.byteLength(files[path])) });
const children = path => {
    const prefix = path === '/' ? '/' : path + '/';
    return [...nodes.keys()].filter(candidate => candidate !== path && candidate.startsWith(prefix) && !candidate.slice(prefix.length).includes('/')).sort();
};
const selfMount = {
    id: 'd28-self/v1', prefix: '/self',
    visible: async () => true,
    stat: async path => { const node = nodes.get(path); if (!node) throw Object.assign(Error('EXPLORATION_NOT_FOUND'), { code: 'EXPLORATION_NOT_FOUND' }); return node; },
    list: async (path, _scope, view, request) => {
        const items = children(path).map(child => nodes.get(child)).slice(0, request.maxItems);
        return { items, total: String(children(path).length), revision: view.revision, responseBytes: 0 };
    },
    open: async node => ({ value: { node, read: async function* () { yield Buffer.from(files[node.path]); } }, release: async () => {} }),
    collect: async () => { throw Error('UNUSED'); }
};

async function shellEnv() {
    const env = await setup();
    try {
        env.ctx.exploration.register(selfMount);
        await env.host.ctx.plugin(ExplorationShell, { timeoutMs: 120000 });
        let ctx;
        await env.host.ctx.plugin({ inject: ['explorationShell', 'exploration', 'tasks', 'database'], apply(c) { ctx = c; } });
        const base = env.context('shell-' + Math.random().toString(36).slice(2));
        const context = { ...base, actionRegistrationId: 'shell', transaction: (request, body) => ctx.database.transaction(base.writeLease, request, body) };
        const session = await ctx.exploration.acquire(scope, context);
        return { env, ctx, context, session };
    }
    catch (error) { await env.close(); throw error; }
}
async function close(f) { try { await f.session.release(); } catch {} await f.env.close(); }
const run = (f, command) => f.ctx.explorationShell.execute(command, f.session.value, f.context);

test('D28 虚拟 /dev/null、/dev/stdout、/dev/stderr 支持常见重定向', async () => {
    const f = await shellEnv();
    try {
        for (const command of ['echo hidden >/dev/null', 'echo hidden >/dev/null 2>&1', 'echo hidden >/dev/null 2>/dev/null']) {
            const result = await run(f, command);
            assert.equal(result.exitCode, 0, command);
            assert.equal(result.stdout, '', command);
            assert.equal(result.stderr, '', command);
        }
        const stderrOnly = await run(f, 'echo hidden 2>/dev/null');
        assert.equal(stderrOnly.exitCode, 0);
        assert.equal(stderrOnly.stdout, 'hidden\n');
        assert.equal(stderrOnly.stderr, '');
        const discarded = await run(f, 'echo hidden >/dev/null; ls /dev/null /dev/stdout /dev/stderr');
        assert.equal(discarded.exitCode, 0);
        assert.equal(discarded.stdout, '/dev/null\n/dev/stderr\n/dev/stdout\n');
        const merged = await run(f, 'echo out; echo err >&2 2>&1');
        assert.equal(merged.exitCode, 0);
        assert.equal(merged.stdout, 'out\n');
        assert.equal(merged.stderr, 'err\n');
        const devices = await run(f, 'cat /dev/null; echo to-stdout >/dev/stdout; echo to-stderr >/dev/stderr');
        assert.equal(devices.exitCode, 0);
        assert.equal(devices.stdout, 'to-stdout\n');
        assert.equal(devices.stderr, 'to-stderr\n');
        // 只读挂载语义不变：写到 /self 下仍被拒绝，且不产生文件。
        const denied = await run(f, 'echo nope > /self/econ/out.txt').catch(error => error);
        if (denied instanceof Error) assert.match(String(denied), /READ_ONLY|EXPLORATION|MOUNT/);
        else assert.notEqual(denied.exitCode, 0);
        await assert.rejects(f.session.value.stat('/self/econ/out.txt'), /NOT_FOUND|NOT_VISIBLE|MISSING/);
        console.log(JSON.stringify({ case: 'D28-T1', redirects: true, readOnlyKept: true }));
    }
    finally { await close(f); }
});

test('D28 run 31/32 真实命令不再因 /dev/null 失败', async () => {
    const f = await shellEnv();
    try {
        const find = await run(f, 'find /self/actions -type f 2>/dev/null | head -20');
        assert.equal(find.exitCode, 0);
        assert.equal(find.stdout, '/self/actions/offers.jsonl\n');
        assert.equal(find.stderr, '');

        const agreements = await run(f, 'cat /self/econ/agreements.jsonl 2>/dev/null');
        assert.equal(agreements.exitCode, 0);
        assert.equal(agreements.stdout, files['/self/econ/agreements.jsonl']);

        const missing = await run(f, 'cat /self/econ/missing.jsonl 2>/dev/null; echo ok');
        assert.equal(missing.exitCode, 0);
        assert.equal(missing.stdout, 'ok\n');
        assert.equal(missing.stderr, '');
        console.log(JSON.stringify({ case: 'D28-T2', find: find.stdout.trim(), agreements: true, missing: true }));
    }
    finally { await close(f); }
});
