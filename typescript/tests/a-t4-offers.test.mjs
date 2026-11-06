import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setup, read, request, scope } from './pr07-support.mjs';
import { offerKey, targetText } from '../.build/core/dist/contracts/affordance.js';
import { actionsReceiptsMigration } from '../.build/core/dist/actions/index.js';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { ExecutionFence } from '../.build/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../.build/core/dist/sqlite/index.js';
import { Initializers } from '../.build/core/dist/initializers/index.js';
import { SqliteBootstrap } from '../.build/core/dist/sqlite-bootstrap/index.js';
import { SqliteHistory } from '../.build/core/dist/sqlite-history/index.js';
import { ArtifactsLocal, artifactsMigration } from '../.build/core/dist/artifacts-local/index.js';

const t1 = { kind: 'test/item', id: 't1' };
const t2 = { kind: 'test/item', id: 't2' };
const t9 = { kind: 'test/item', id: 't9' };
const limits = { maxOffers: 64, maxOfferBytes: 1048576, maxSchemaBytes: 4096 };
const completed = (context, value) => ({ operationId: context.operationId, status: 'completed', value, original: null, artifacts: [] });
const card = (name, extra = {}) => ({ name, inputSchema: { type: 'object' }, outputSchema: {}, effect: 'read', available: async () => true, execute: async (input, context) => completed(context, input), ...extra });

test('A-T4 offer 层、目标复查、拒绝回执与 v2 回执表', async (t) => {
    const env = await setup();
    const { ctx } = env;
    let published = true;
    const actions = [
        card('test.ambient', { description: '无目标行动' }),
        card('test.act', { description: '带目标行动', targets: { kinds: ['test/item'], required: true }, inputSchema: { type: 'object', properties: { itemId: { type: 'string' }, quantity: { type: 'integer' } }, required: ['itemId'] } }),
    ];
    const world = card('test.world', {
        targets: { kinds: ['test/item'], required: true }, effect: 'world',
        inputSchema: { type: 'object', properties: { itemId: { type: 'string' }, quantity: { type: 'integer' }, revoke: { type: 'boolean' } }, required: ['itemId'], additionalProperties: false },
        async execute(input, context) {
            if (input.revoke)
                published = false;
            const { receipt } = await context.transaction(request(context.operationId), async () => ({ ok: true }));
            return { operationId: context.operationId, status: 'completed', value: { ok: true }, original: { revision: receipt.revision }, artifacts: [] };
        },
    });
    for (const action of [...actions, world])
        ctx.actions.register(action);
    ctx.actorEligibility.register({
        id: 'a4-targets', targetAware: true,
        async evaluate(request) {
            const denied = request.target?.kind === 'test/item' && request.target.id === 't2';
            return { allowed: !denied, reason: denied ? 'target-denied' : 'ok', facts: {}, versions: [] };
        },
    });
    const p1 = {
        id: 'p1', actions: ['test.act', 'test.world'],
        versions: async () => [{ owner: 'p1', version: '1' }],
        offers: async () => {
            if (!published)
                return [];
            return [
                { action: 'test.act', target: t1, prefill: { itemId: 't1' }, needs: ['quantity'], reason: 'r1' },
                { action: 'test.act', target: t2, reason: 'r2' },
                { action: 'test.world', target: t1, prefill: { itemId: 't1' }, locked: ['itemId'], reason: '可执行' },
            ];
        },
    };
    const p2 = {
        id: 'p2', actions: ['test.act'],
        versions: async () => [{ owner: 'p2', version: '1' }],
        offers: async () => [{ action: 'test.act', target: t1, prefill: { itemId: 't1' }, reason: 'p2', priority: 10 }],
    };
    const p3 = {
        id: 'p3', actions: ['test.act'],
        versions: async () => [],
        offers: async () => [{ action: 'test.nope', target: t1 }],
    };
    const disposers = [ctx.actions.provide(p1)];
    try {
        await t.test('门槛与 available 过滤目标，排序与重复计算确定', async () => {
            const set = await read(ctx, (view) => ctx.actions.offers(scope, view, limits));
            assert.deepEqual(set.offers.map(offerKey), ['plain.update@-', offerKey({ action: 'test.act', target: t1 }), 'test.ambient@-', offerKey({ action: 'test.world', target: t1 })]);
            assert.ok(set.schemas.get('test.act'));
            assert.equal(set.offers.some((offer) => offer.target?.id === 't2'), false);
            const twice = await read(ctx, (view) => ctx.actions.offers(scope, view, limits));
            assert.equal(JSON.stringify(set.offers), JSON.stringify(twice.offers));
            ctx.actions.metrics.eligibilityScans = 0;
            await read(ctx, (view) => ctx.actions.offers(scope, view, limits));
            assert.equal(ctx.actions.metrics.eligibilityScans, 7);
        });

        await t.test('同 key 取 priority 高者，provider 与 reason 合并', async () => {
            disposers.push(ctx.actions.provide(p2));
            const set = await read(ctx, (view) => ctx.actions.offers(scope, view, limits));
            const same = set.offers.filter((offer) => offerKey(offer) === offerKey({ action: 'test.act', target: t1 }));
            assert.equal(same.length, 1);
            assert.equal(same[0].provider, 'p2');
            assert.match(same[0].reason, /p2/);
            assert.match(same[0].reason, /r1|p1/);
        });

        await t.test('provider 产出未声明行动报 OFFER_UNDECLARED', async () => {
            const disposer = ctx.actions.provide(p3);
            try {
                await assert.rejects(read(ctx, (view) => ctx.actions.offers(scope, view, limits)), (error) => error.code === 'OFFER_UNDECLARED');
            }
            finally {
                disposer();
            }
        });

        await t.test('目标、锁定字段与回执 v2', async () => {
            const receipt = await ctx.actions.execute('test.world', { quantity: 2 }, env.context('a4-valid'), t1);
            assert.equal(receipt.status, 'completed');
            const rows = await read(ctx, (view) => view.query({ sql: 'SELECT target,offer_key,provider FROM action_receipts WHERE operation_id=?', parameters: ['a4-valid'] }, { maxRows: 1, maxBytes: 4096 }));
            assert.equal(rows[0].target, targetText(t1));
            assert.equal(rows[0].offer_key, offerKey({ action: 'test.world', target: t1 }));
            assert.equal(rows[0].provider, 'p1');

            const locked = await ctx.actions.execute('test.world', { itemId: 'other' }, env.context('a4-locked'), t1);
            assert.equal(locked.status, 'rejected');
            assert.equal(locked.value.code, 'ACTION_INPUT_LOCKED');

            const missing = await ctx.actions.execute('test.world', { itemId: 't9' }, env.context('a4-not-offered'), t9);
            assert.equal(missing.status, 'rejected');
            assert.equal(missing.value.code, 'ACTION_NOT_OFFERED');
            assert.deepEqual(missing.value.alternatives.map((ref) => ref.target), [targetText(t1)]);
            const identity = { lineage: 'pr07', operationId: 'action-run:' + JSON.stringify(['pr07', 'a4-not-offered']), attempt: 0 };
            assert.equal((await ctx.attempts.probe(identity)).status, 'absent');
        });

        await t.test('初检与最终事务之间 offer 消失：抛出 ACTION_INELIGIBLE 且不写回执', async () => {
            await assert.rejects(ctx.actions.execute('test.world', { quantity: 1, revoke: true }, env.context('a4-revoked'), t1), (error) => error.code === 'ACTION_INELIGIBLE');
            published = true;
            const rows = await read(ctx, (view) => view.query({ sql: 'SELECT count(*) AS n FROM action_receipts WHERE operation_id=?', parameters: ['a4-revoked'] }, { maxRows: 1, maxBytes: 1024 }));
            assert.equal(rows[0].n, 0n);
        });

        await t.test('20 个行动、3 个 provider 的 offers 耗时记录', async () => {
            for (let index = 0; index < 20; index++)
                ctx.actions.register(card(`perf.${String(index).padStart(2, '0')}`));
            for (const id of ['perf-a', 'perf-b', 'perf-c'])
                ctx.actions.provide({ id, actions: [], versions: async () => [], offers: async () => [] });
            const samples = [];
            for (let round = 0; round < 30; round++) {
                const started = performance.now();
                await read(ctx, (view) => ctx.actions.offers(scope, view, limits));
                samples.push(performance.now() - started);
            }
            samples.sort((left, right) => left - right);
            const p50 = samples[Math.floor(samples.length * 0.5)], p99 = samples.at(-1);
            console.log(JSON.stringify({ offersMs: { samples: samples.length, p50, p99 } }));
            assert.ok(p50 >= 0 && p99 >= p50);
        });
    }
    finally {
        for (const dispose of disposers.reverse())
            await dispose();
        await env.close();
    }
});

test('A-T4 v1 回执表迁移到 v2', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'a4-migration-'));
    const host = await createHost({ boot: [] });
    try {
        await host.ctx.plugin(OwnedResources);
        await host.ctx.plugin(TaskSupervisor, { concurrency: 2, maxBytes: 33554432 });
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(ExecutionFence);
        await host.ctx.plugin(SqliteDatabase, { directory, runId: 'a4-migration', generation: 'g', maxTransactionMs: 5000 });
        await host.ctx.plugin(ArtifactsLocal, { directory: join(directory, 'objects') });
        await host.ctx.plugin(Initializers);
        await host.ctx.plugin(SqliteBootstrap);
        await host.ctx.plugin(SqliteHistory, { directory, runId: 'a4-migration', generation: 'g', controlSequence: '0' });
        const source = { consumer: 'a4-migration' };
        await host.ctx.database.applyMigrations([artifactsMigration,
            { owner: 'actions', from: 0, to: 1, after: [], source, statements: [{ sql: 'CREATE TABLE action_receipts(operation_id TEXT PRIMARY KEY NOT NULL,invocation_id TEXT NOT NULL,actor_id TEXT NOT NULL,moment_id TEXT NOT NULL,action TEXT NOT NULL,registration_id TEXT NOT NULL,input TEXT NOT NULL,value TEXT NOT NULL)' }] },
            actionsReceiptsMigration(source),
        ]);
        const view = await host.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const rows = await view.query({ sql: "SELECT sql FROM sqlite_master WHERE type='table' AND name='action_receipts'" }, { maxRows: 1, maxBytes: 16384 });
            assert.match(String(rows[0].sql), /target TEXT NULL/);
            assert.match(String(rows[0].sql), /offer_key TEXT NULL/);
            assert.match(String(rows[0].sql), /provider TEXT NULL/);
        }
        finally {
            await view.close();
        }
    }
    finally {
        await host.close();
    }
});
