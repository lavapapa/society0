import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup, read, request } from './pr07-support.mjs';
import PublicSelf from '../.build/core/dist/public-self/index.js';
import ActionFiles, { SELF_ACTION_LIMITS } from '../.build/core/dist/action-files/index.js';
import { emptyOfferFold } from '../.build/core/dist/contracts/affordance.js';
import { offerDelivery } from '../.build/core/dist/driver-llm/offers.js';

// A-T8：/self 下的行动清单文件视图。offers.jsonl 是当前主体的 offer 集，
// catalog.jsonl 是这些行动当前的 schema 卡片。两者都只从 actions.offers()
// 与 actions.describe() 读取，不另存状态。

const offersPath = '/self/actions/offers.jsonl';
const catalogPath = '/self/actions/catalog.jsonl';
const listPath = '/self/actions/offers.jsonl';

const stateMigration = {
    owner: 'a8-state', from: 0, to: 1, after: [], source: { consumer: 'a8-self-actions' },
    statements: [{ sql: 'CREATE TABLE a8_state(actor_id TEXT PRIMARY KEY NOT NULL, enabled INTEGER NOT NULL)' }],
};

const completed = (context, value) => ({ operationId: context.operationId, status: 'completed', value, original: null, artifacts: [] });

function action() {
    return {
        name: 'test.dynamic',
        description: '按状态出现的行动',
        inputSchema: { type: 'object', properties: { itemId: { type: 'string' } }, required: ['itemId'], additionalProperties: false },
        outputSchema: {},
        effect: 'world',
        targets: { kinds: ['test/item'], required: true },
        available: async () => true,
        async execute(input, context) {
            const { receipt } = await context.transaction(request(context.operationId), async (tx) => {
                await tx.execute({ sql: 'INSERT INTO a8_state(actor_id,enabled) VALUES(?,1) ON CONFLICT(actor_id) DO UPDATE SET enabled=1', parameters: [context.actorId] });
                return { ok: true };
            });
            return { ...completed(context, { ok: true }), original: { revision: receipt.revision } };
        },
    };
}

function provider() {
    return {
        id: 'a8',
        actions: ['test.dynamic'],
        versions: async () => [{ owner: 'a8', version: '1' }],
        async offers(query, reader) {
            const [row] = await reader.query({ sql: 'SELECT enabled FROM a8_state WHERE actor_id=?', parameters: [query.actorId] }, { maxRows: 1, maxBytes: 1024 });
            if (row)
                return [];
            const target = (suffix) => ({ kind: 'test/item', id: `${query.actorId}-${suffix}` });
            const item = (suffix) => ({ action: 'test.dynamic', target: target(suffix), prefill: { itemId: `${query.actorId}-${suffix}` }, locked: ['itemId'], needs: ['itemId'], reason: `当前主体 ${query.actorId} 可执行 ${suffix}` });
            return [item('1'), item('2')];
        },
    };
}

const scopeOf = (actorId, momentId = 'm') => ({ actorId, momentId, query: null });
const acquisitionOf = (run, actorId, momentId, operationId) => ({
    ctx: run.ctx, database: run.ctx.database, writeLease: run.reservation.lease,
    actorId, momentId, logicalTime: 1, runId: 'pr07', generation: 'g', operationId, signal: run.reservation.signal,
});

async function open(run, actorId, momentId = 'm') {
    return run.ctx.exploration.acquire(scopeOf(actorId, momentId), acquisitionOf(run, actorId, momentId, `a8-read-${actorId}-${momentId}`));
}

async function readFile(session, path) {
    const node = await session.stat(path);
    const handle = await session.open(node);
    const chunks = [];
    try {
        for await (const chunk of handle.value.read({ offset: '0', length: Number(node.rawBytes), maxBytes: 32 * 1024 * 1024 }, new AbortController().signal))
            chunks.push(Buffer.from(chunk));
    }
    finally {
        await handle.release();
    }
    return { node, text: Buffer.concat(chunks).toString('utf8') };
}

function refs(set) {
    return set.offers.map(offer => ({
        act: offer.action,
        ...(offer.target ? { target: `${offer.target.kind}:${offer.target.id}` } : {}),
        ...(offer.prefill !== undefined ? { prefill: offer.prefill } : {}),
        ...(offer.needs ? { needs: offer.needs } : {}),
    }));
}

function lines(text) {
    return text.split('\n').filter(line => line !== '').map(line => JSON.parse(line));
}

async function fixture() {
    const run = await setup({ migrations: [stateMigration] });
    try {
        await run.host.ctx.plugin(PublicSelf, { mount: '/self' });
        await run.host.ctx.plugin(ActionFiles);
        const disposeAction = run.ctx.actions.register(action());
        const disposeProvider = run.ctx.actions.provide(provider());
        return {
            run,
            async close() {
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

test('A-T8 /self/actions 文件视图', async (t) => {
    const env = await fixture();
    const { run } = env;
    let session;
    try {
        await t.test('offers.jsonl 每行与 offers() 往返无损', async () => {
            session = await open(run, 'a');
            const before = await read(run.ctx, view => run.ctx.actions.offers(scopeOf('a'), view, SELF_ACTION_LIMITS));
            const file = await readFile(session.value, offersPath);
            assert.deepEqual(lines(file.text), refs(before), 'offers.jsonl 必须逐行等于当前 offers()');
            assert.ok(file.node.rawBytes !== undefined);
        });

        await t.test('catalog.jsonl 的 schema 卡片与 describe() 一致', async () => {
            const cards = lines((await readFile(session.value, catalogPath)).text);
            const names = [...new Set(cards.map(card => card.action))].sort();
            assert.deepEqual(names, ['test.dynamic'], 'catalog 覆盖当前 offer 集里出现的行动');
            for (const card of cards)
                assert.deepEqual(card, await run.ctx.actions.describe(card.action), 'schema 卡片必须等于 describe()');
        });

        await t.test('变更段的完整清单路径存在且可读', async () => {
            const view = await run.ctx.database.snapshot({ maxAgeMs: 30000 });
            let text;
            try {
                text = (await offerDelivery(run.ctx.actions, scopeOf('a'), view, emptyOfferFold(), { ...SELF_ACTION_LIMITS, maxOffers: 1 })).text;
            }
            finally {
                await view.close();
            }
            assert.match(text, /另有/);
            assert.ok(text.includes(listPath), '变更段必须指向 /self/actions/offers.jsonl');
            const file = await readFile(session.value, listPath);
            assert.deepEqual(lines(file.text), lines((await readFile(session.value, offersPath)).text), '完整清单就是 offers.jsonl 的当前内容');
        });

        await t.test('A 与 B 的 offers.jsonl 互不相同', async () => {
            const other = await open(run, 'b');
            try {
                const a = await readFile(session.value, offersPath);
                const b = await readFile(other.value, offersPath);
                assert.notEqual(a.text, b.text);
                assert.deepEqual(lines(a.text).map(ref => ref.target), ['test/item:a-1', 'test/item:a-2']);
                assert.deepEqual(lines(b.text).map(ref => ref.target), ['test/item:b-1', 'test/item:b-2']);
            }
            finally {
                await other.release();
            }
        });

        await t.test('同一 moment 内执行行动后下一次读取反映新的 offer 集', async () => {
            const receipt = await run.ctx.actions.execute('test.dynamic', { itemId: 'a-1' }, acquisitionOf(run, 'a', 'm', 'a8-execute'), { kind: 'test/item', id: 'a-1' });
            assert.equal(receipt.status, 'completed');
            const after = await readFile(session.value, offersPath);
            assert.equal(after.text, '', '执行后该主体的 offer 集为空');
            const catalog = await readFile(session.value, catalogPath);
            assert.equal(catalog.text, '', '空 offer 集对应空 catalog');
            assert.equal(after.node.path, offersPath, '没有 offer 时路径仍然可见，内容为空');
            const other = await open(run, 'b');
            try {
                assert.equal(lines((await readFile(other.value, offersPath)).text).length, 2, 'B 的 offer 不受 A 的执行影响');
            }
            finally {
                await other.release();
            }
        });
    }
    finally {
        try { await session?.release(); } catch { }
        await env.close();
    }
});
