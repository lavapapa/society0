// cases: D32 /self/actions 在 run 38 规模下受具名预算约束；public-self 超限截断；shell 对截断返回 exit 0
import test from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './pr07-support.mjs';
import PublicSelf from '../.build/core/dist/public-self/index.js';
import ActionFiles, { SELF_ACTION_LIMITS, SELF_ACTION_MAX_BYTES, SELF_ACTION_MAX_OFFERS } from '../.build/core/dist/action-files/index.js';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

const OFFERS = 200;
const offersPath = '/self/actions/offers.jsonl';
const catalogPath = '/self/actions/catalog.jsonl';
const truncatedPath = '/self/d32/truncated.jsonl';

function action() {
    return {
        name: 'd32.market', description: '按挂单买入', effect: 'read',
        inputSchema: { type: 'object', properties: { qty: { type: 'string' } }, required: ['qty'], additionalProperties: false },
        outputSchema: {},
        targets: { kinds: ['d32/offer'], required: true },
        available: async () => true,
        execute: async (input, context) => ({ operationId: context.operationId, status: 'completed', value: input, original: null, artifacts: [] }),
    };
}

// 复现 run 38 的失败形状：offer provider 把 limits.maxOffers 直接当 SQL maxRows。
function provider() {
    return {
        id: 'd32-market', actions: ['d32.market'],
        versions: async () => [{ owner: 'd32', version: '1' }],
        async offers(query, reader) {
            await reader.query({ sql: 'SELECT 1 AS ok' }, { maxRows: Math.max(1, query.maxItems), maxBytes: 1024 * 1024 });
            return Array.from({ length: OFFERS }, (_, index) => ({
                action: 'd32.market',
                target: { kind: 'd32/offer', id: String(index) },
                prefill: { qty: String(index + 1) },
                locked: ['qty'],
                needs: ['qty'],
                reason: `第 ${index} 条挂单`,
            }));
        },
    };
}

function truncatedProvider() {
    return {
        path: truncatedPath, media: 'application/jsonl',
        versions: async () => [{ owner: 'd32', version: 'truncated' }],
        async *render() {
            yield Buffer.from('{"ok":1}\n');
            throw Object.assign(Error('SQLITE_QUERY_BUDGET: maxRows 10001 > 10000'), { code: 'SQLITE_QUERY_BUDGET' });
        },
    };
}

function contextOf(run) {
    const base = run.context('d32-explore');
    return { ...base, actionRegistrationId: 'd32-shell', transaction: (requested, body) => run.ctx.database.transaction(base.writeLease, requested, body) };
}

async function fixture() {
    const run = await setup();
    try {
        await run.host.ctx.plugin(PublicSelf, { mount: '/self' });
        await run.host.ctx.plugin(ActionFiles);
        await run.host.ctx.plugin(ExplorationShell, { timeoutMs: 120000, maxFileBytes: 1048576, maxOutputBytes: 4194304, maxRpcBytes: 8388608 });
        const shell = run.host.ctx;
        const disposeAction = run.ctx.actions.register(action());
        const disposeProvider = run.ctx.actions.provide(provider());
        const disposeTruncated = run.host.ctx.publicSelf.provide(truncatedProvider());
        return {
            run, shell,
            async close() {
                await disposeTruncated();
                await disposeProvider();
                await disposeAction();
                await run.close();
            },
        };
    }
    catch (error) {
        await run.close();
        throw error;
    }
}

async function open(run) {
    return run.ctx.exploration.acquire({ actorId: 'a', momentId: 'm', query: null }, contextOf(run));
}

async function readFile(session, path) {
    const node = await session.stat(path);
    const handle = await session.open(node);
    const chunks = [];
    try {
        for await (const chunk of handle.value.read({ offset: '0', length: Number(node.rawBytes), maxBytes: 8 * 1024 * 1024 }, new AbortController().signal))
            chunks.push(Buffer.from(chunk));
    }
    finally {
        await handle.release();
    }
    return { node, text: Buffer.concat(chunks).toString('utf8') };
}

test('D32 SELF_ACTION_LIMITS 是具名有界值，不是安全整数上限', () => {
    assert.equal(SELF_ACTION_MAX_OFFERS, 10000);
    assert.equal(SELF_ACTION_MAX_BYTES, 1048576);
    assert.equal(SELF_ACTION_LIMITS.maxOffers, SELF_ACTION_MAX_OFFERS);
    assert.equal(SELF_ACTION_LIMITS.maxOfferBytes, SELF_ACTION_MAX_BYTES);
    assert.equal(SELF_ACTION_LIMITS.maxSchemaBytes, SELF_ACTION_MAX_BYTES);
    for (const value of Object.values(SELF_ACTION_LIMITS))
        assert.notEqual(value, Number.MAX_SAFE_INTEGER);
});

test('D32 run 38 规模 offers.jsonl 在默认预算下可 stat/read，ls/wc/head/cat/grep/find 全部 exit 0', async t => {
    const env = await fixture();
    const { run, shell } = env;
    let session;
    try {
        session = await open(run);
        const offers = await readFile(session.value, offersPath);
        const lines = offers.text.split('\n').filter(Boolean).map(line => JSON.parse(line));
        assert.equal(lines.filter(line => line.act === 'd32.market').length, OFFERS, 'offers.jsonl 仍列出全部可用行动');
        assert.ok(Number(offers.node.rawBytes) < SELF_ACTION_MAX_BYTES);
        for (const line of lines.filter(line => line.act === 'd32.market')) {
            assert.equal(line.summary, '按挂单买入');
            assert.equal(line.inputSchema, undefined, 'offers.jsonl 不放完整 schema');
        }
        const catalog = await readFile(session.value, catalogPath);
        assert.match(catalog.text, /"input"/);

        const context = contextOf(run);
        const commands = [
            'ls -la /self/actions/offers.jsonl',
            'wc -c /self/actions/offers.jsonl',
            'head -c 500 /self/actions/offers.jsonl',
            'cat /self/actions/offers.jsonl',
            'grep d32.market /self/actions/offers.jsonl',
            'find /self',
        ];
        for (const command of commands) {
            const result = await shell.explorationShell.execute(command, session.value, context);
            assert.equal(result.exitCode, 0, `${command}\n${result.stderr}`);
        }
        const wc = await shell.explorationShell.execute('wc -c /self/actions/offers.jsonl', session.value, context);
        assert.ok(Number(wc.stdout.trim().split(/\s+/)[0]) > 0);
        const cat = await shell.explorationShell.execute('cat /self/actions/offers.jsonl', session.value, context);
        assert.equal(cat.stdout.trim().split('\n').filter(line => line.includes('"act":"d32.market"')).length, OFFERS);
    }
    finally {
        try { await session?.release(); } catch { }
        await env.close();
    }
});

test('D32 public-self 渲染超预算时截断并附说明，stat 与 shell 读取不抛错', async t => {
    const env = await fixture();
    const { run, shell } = env;
    let session;
    try {
        session = await open(run);
        const file = await readFile(session.value, truncatedPath);
        assert.match(file.text, /^\{"ok":1\}/);
        assert.match(file.text, /"truncated":true/);
        assert.match(file.text, /已截断/);
        const context = contextOf(run);
        const cat = await shell.explorationShell.execute('cat ' + truncatedPath, session.value, context);
        assert.equal(cat.exitCode, 0, cat.stderr);
        assert.match(cat.stdout, /已截断/);
        const find = await shell.explorationShell.execute('find /self', session.value, context);
        assert.equal(find.exitCode, 0, find.stderr);
    }
    finally {
        try { await session?.release(); } catch { }
        await env.close();
    }
});
