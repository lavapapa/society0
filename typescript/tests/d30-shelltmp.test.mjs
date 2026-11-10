import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, scope } from './pr07-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

const version = [{ owner: 'd30-self', version: '1' }];
const offers = '{"id":"offer-1","kind":"contract"}\n{"id":"offer-2","kind":"quote"}\n';
const files = {
    '/self/actions/offers.jsonl': offers,
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
    id: 'd30-self/v1', prefix: '/self',
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

// run 31 cell_head 原命令。
const contractCommand = 'cat /self/actions/offers.jsonl | grep contract';
// run 31 cathode_makers 原命令（完整长 JSON）。
const planCommand = String.raw`echo '{"content":"{\n  \"sales\": [\n    {\"product\": \"lfp\", \"targetPrice\": 46500, \"volume\": 1000, \"note\": \"销售磷酸铁锂，目标价46500元/吨\"}\n  ],\n  \"procurement\": [\n    {\"product\": \"carbonate\", \"maxPrice\": 88000, \"volume\": 250, \"note\": \"采购碳酸锂，最高价88000元/吨，用于生产1000吨LFP\"}\n  ],\n  \"production\": [\n    {\"recipe\": \"rc_lfp\", \"activities\": 1000, \"note\": \"生产磷酸铁锂，需250吨碳酸锂原料\"}\n  ]\n}"}' > /tmp/plan_update.json`;
// run 32 lithium_mines 原命令。
const strategyCommand = 'echo "Memory write skipped due to budget limit. Strategy: Proposed supply agreements to salt_head (10,500 CNY/t) and salt_plants (10,400 CNY/t) for spodumene. No inventory yet." > /tmp/strategy_note.txt';

test('D30 管道 stdin 交给 grep，不拿默认根搜索', async () => {
    const f = await shellEnv();
    try {
        const piped = await run(f, contractCommand);
        assert.equal(piped.exitCode, 0);
        assert.equal(piped.stdout, '{"id":"offer-1","kind":"contract"}\n');
        assert.doesNotMatch(piped.stderr, /EXPLORATION_MOUNT_MISSING/);

        // 上游失败时管道为空：仍应读 stdin，而不是回落到默认根。
        const empty = await run(f, 'cat /self/actions/missing.jsonl | grep contract');
        assert.equal(empty.exitCode, 1);
        assert.match(empty.stderr, /No such file/);
        assert.doesNotMatch(empty.stderr, /EXPLORATION_MOUNT_MISSING/);
        console.log(JSON.stringify({ case: 'D30-T1', piped: piped.stdout.trim(), emptyPipe: empty.exitCode }));
    }
    finally { await close(f); }
});

test('D30 run 31/32 的真实 /tmp 写入在同一激活内可读', async () => {
    const f = await shellEnv();
    try {
        const plan = await run(f, planCommand);
        assert.equal(plan.exitCode, 0);
        assert.equal(plan.stderr, '');
        const planText = await run(f, 'cat /tmp/plan_update.json');
        assert.equal(planText.exitCode, 0);
        assert.match(planText.stdout, /sales/);
        assert.match(planText.stdout, /46500/);

        const strategy = await run(f, strategyCommand);
        assert.equal(strategy.exitCode, 0);
        assert.equal(strategy.stderr, '');
        const strategyText = await run(f, 'cat /tmp/strategy_note.txt');
        assert.equal(strategyText.exitCode, 0);
        assert.equal(strategyText.stdout, 'Memory write skipped due to budget limit. Strategy: Proposed supply agreements to salt_head (10,500 CNY/t) and salt_plants (10,400 CNY/t) for spodumene. No inventory yet.\n');
        console.log(JSON.stringify({ case: 'D30-T2', plan: planText.stdout.length, strategy: strategyText.stdout.length }));
    }
    finally { await close(f); }
});

test('D30 /tmp 不跨激活：第二次激活读不到上一次的文件', async () => {
    const first = await shellEnv();
    try {
        const written = await run(first, 'echo draft > /tmp/isolation.txt');
        assert.equal(written.exitCode, 0);
        const visible = await run(first, 'cat /tmp/isolation.txt');
        assert.equal(visible.stdout, 'draft\n');
    }
    finally { await close(first); }
    const second = await shellEnv();
    try {
        const missing = await run(second, 'cat /tmp/isolation.txt');
        assert.notEqual(missing.exitCode, 0);
        assert.match(missing.stderr, /No such file/);
        assert.doesNotMatch(missing.stderr, /EXPLORATION_MOUNT_MISSING/);
        const listed = await run(second, 'ls /tmp');
        assert.equal(listed.exitCode, 0);
        assert.equal(listed.stdout, '');
        console.log(JSON.stringify({ case: 'D30-T3', isolated: true, list: listed.stdout }));
    }
    finally { await close(second); }
});

test('D30 /tmp 超限返回可读错误，/self 仍只读', async () => {
    const f = await shellEnv();
    try {
        // 单次命令的输出预算 64 KiB，用多次执行累计越过 1 MiB；同一会话的 /tmp 应一直保留。
        const chunk = 'x'.repeat(1100);
        let overflow;
        for (let index = 0; index < 18; index++) {
            const command = `s='${chunk}'; i=0; while [ $i -lt 58 ]; do echo "$s" ${index === 0 ? '>' : '>>'} /tmp/big; i=$((i+1)); done`;
            overflow = await run(f, command).catch(error => error);
            if (overflow instanceof Error || overflow.exitCode !== 0) break;
        }
        if (overflow instanceof Error) assert.match(String(overflow), /SHELL_TMP_BYTES/);
        else {
            assert.notEqual(overflow.exitCode, 0, JSON.stringify(overflow));
            assert.match(overflow.stderr, /SHELL_TMP_BYTES/);
            assert.doesNotMatch(overflow.stderr, /EXPLORATION_MOUNT_MISSING/);
        }
        const bytes = await run(f, 'wc -c /tmp/big');
        assert.equal(bytes.exitCode, 0);
        assert.ok(Number(bytes.stdout.trim().split(/\s+/)[0]) <= 1048576, bytes.stdout);

        const files = Array.from({ length: 64 }, (_, index) => `/tmp/f${index + 1}`).join(' ');
        const tooMany = await run(f, `touch ${files}`).catch(error => error);
        if (tooMany instanceof Error) assert.match(String(tooMany), /SHELL_TMP_FILES/);
        else {
            assert.notEqual(tooMany.exitCode, 0, JSON.stringify(tooMany));
            assert.match(tooMany.stderr, /SHELL_TMP_FILES/);
            assert.doesNotMatch(tooMany.stderr, /EXPLORATION_MOUNT_MISSING/);
        }

        const denied = await run(f, 'echo nope > /self/econ/out.txt').catch(error => error);
        if (denied instanceof Error) assert.match(String(denied), /READ_ONLY|EXPLORATION|MOUNT/);
        else assert.notEqual(denied.exitCode, 0);
        await assert.rejects(f.session.value.stat('/self/econ/out.txt'), /NOT_FOUND|NOT_VISIBLE|MISSING/);
        console.log(JSON.stringify({ case: 'D30-T4', limit: 'SHELL_TMP_BYTES', readOnlyKept: true }));
    }
    finally { await close(f); }
});
