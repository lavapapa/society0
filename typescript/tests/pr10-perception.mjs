import { join, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixture, activation, request, runtime } from './pr10-support.mjs';
import Drivers from '../.build/core/dist/drivers/index.js';
import Perceptions, { perceptionMigration } from '../.build/core/dist/perception/index.js';
import Rule, { ruleHandlerSlot } from '../.build/core/dist/driver-rule/index.js';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';
import SocialPerception from '../.build/social/dist/social-perception/index.js';
import { propagationSlot } from '../.build/social/dist/social/index.js';
test('PR10-T02/T03/T20 narrow third-party propagation, raw pagination and shared rule perception', async () => {
    const f = await fixture({
        propagation: 'third-party/direct', migrations: [perceptionMigration({
                package: '@society0/core', entry: 'perception'
            })]
    });
    let reservation, selection, driver;
    try {
        const c = f.ctx;
        await c.plugin({
            inject: ['slots'], apply(ctx) {
                ctx.slots.contribute(propagationSlot, {
                    id: 'third-party/direct', value: {
                        id: 'third-party/direct', async recipients(input, view) {
                            assert.deepEqual(Object.keys(view).sort(), ['followers', 'neighbors']);
                            return (await view.followers(input.actorId)).filter(id => id === 'c');
                        }
                    }
                });
            }
        });
        for (const plugin of [Drivers, Perceptions, Rankings, Recommendations, SocialPerception])
            await c.plugin(plugin);
        await c.plugin(Rule, {
            handler: 'inspect-social', sources: ['social/profile', 'social/feed', 'social/notifications', 'social/recommendations']
        });
        let observed;
        await c.plugin({
            inject: ['slots'], apply(ctx) {
                ctx.slots.contribute(ruleHandlerSlot, {
                    id: 'inspect-social', value: {
                        async activate(input) {
                            observed = input;
                            return {
                                status: 'completed', value: null, evidence: {}
                            };
                        }
                    }
                });
            }
        });
        reservation = await c.executionFence.reserve({
            id: 'social-perception'
        });
        const context = activation(f, reservation, 'perception', 'c');
        const original = '完整原文、原始标签和元数据。'.repeat(100);
        for (let i = 0; i < 3; i++)
            await c.actions.execute('social.publish', {
                id: 'post-' + i, content: original + i, tags: ['science'], metadata: {
                    raw: {
                        ordinal: i
                    }
                }
            }, {
                ...activation(f, reservation, 'post-' + i), logicalTime: String(9007199254740993n + BigInt(i))
            });
        let view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal((await c.socialRepository.feed('b', view, {
            maxItems: 1, maxResponseBytes: 65536
        })).total, '0');
        const first = await c.socialRepository.feed('c', view, {
            maxItems: 1, maxResponseBytes: 65536
        });
        const second = await c.socialRepository.feed('c', view, {
            maxItems: 1, maxResponseBytes: 65536, cursor: first.next
        });
        assert.equal(first.total, '3');
        assert.equal(second.total, '3');
        assert.equal(first.items[0].content, original + '0');
        assert.equal(second.items[0].metadata.raw.ordinal, 1);
        assert.equal(second.items[0].createdTime, '9007199254740994');
        const trend = await c.socialRepository.trending(view, {
            maxItems: 1, maxResponseBytes: 65536
        }, {
            time: '9007199254740995', window: '100'
        });
        const trend2 = await c.socialRepository.trending(view, {
            maxItems: 1, maxResponseBytes: 65536, cursor: trend.next
        }, {
            time: '9007199254740995', window: '100'
        });
        assert.equal(trend.total, '3');
        assert.notEqual(trend.items[0].id, trend2.items[0].id);
        const thread = await c.threads.open('c', 'first', view);
        await view.close();
        const timed = await c.perceptions.acquire(['social/recommendations'], context);
        try {
            await assert.rejects(timed.value.collect({
                actorId: 'c', momentId: 'first', threadId: thread.id, reason: null
            }, {
                maxItems: 10, maxResponseBytes: 65536
            }, context), /SOCIAL_RECOMMENDATION_TIME_REQUIRED/);
        }
        finally {
            await timed.release();
        }
        selection = await c.perceptions.acquire(['social/feed'], context);
        const batch = await selection.value.collect({
            actorId: 'c', momentId: 'first', threadId: thread.id, reason: null
        }, {
            maxItems: 1, maxResponseBytes: 32768
        }, context);
        assert.equal(batch.reads[0].page.hasMore, true);
        assert.equal(batch.reads[0].page.items[0].material.value.content, original + '0');
        const message = await c.threads.prepare({
            id: 'first-input', interactionId: thread.interactionId, role: 'user', content: batch.reads[0].page.items, originalProviderFields: {
                source: 'social/feed'
            }
        }, context.signal);
        await c.database.transaction(reservation.lease, request('consume-first'), tx => selection.value.commit(tx, batch, [{
                sourceId: 'social/feed', perceptIds: batch.reads[0].page.items.map(x => x.id), message, contexts: batch.reads[0].page.items.map(x => x.material)
            }]));
        await selection.release();
        selection = undefined;
        driver = await c.drivers.acquire('rule', 'g');
        const result = await driver.value.activate({
            id: 'social-rule', actorId: 'c', momentId: 'second', source: 'social-test', driver: 'rule', input: {
                raw: true
            }
        }, {
            ...context, momentId: 'second'
        });
        assert.equal(result.status, 'completed');
        assert.ok(result.requiredOperations.length > 0);
        assert.ok(observed.perceptionBatchIds.length > 0);
        await driver.release();
        driver = undefined;
        view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        const materials = await view.query({
            sql: "SELECT material,thread_id FROM perception_material WHERE source_id='social/feed' ORDER BY percept_id"
        }, {
            maxRows: 10, maxBytes: 65536
        });
        assert.equal(materials.length, 3);
        assert.equal(new Set(materials.map(x => x.thread_id)).size, 2);
        assert.equal(JSON.parse(materials[2].material).value.content, original + '2');
        const [links] = await view.query({
            sql: 'SELECT (SELECT count(*) FROM perception_material) AS materials,(SELECT count(*) FROM perception_links) AS links'
        }, {
            maxRows: 1, maxBytes: 2048
        });
        assert.equal(links.materials, links.links);
        assert.equal((await c.socialRepository.get('post-0', view)).views, '0');
        assert.equal((await c.socialRepository.notifications('c', view, {
            maxItems: 10, maxResponseBytes: 65536
        }, true)).total, '3');
        await view.close();
    }
    finally {
        await driver?.release();
        await selection?.release();
        await reservation?.release();
        await f.host.close();
    }
});
test('PR10-T05/T20 independent fork resumes Social feed cursor and persisted conversation thread', async () => {
    const migration = perceptionMigration({
        package: '@society0/core', entry: 'perception'
    });
    const f = await fixture({
        migrations: [migration]
    });
    let branch;
    const proposal = (id, actorId, momentId, driver, next) => ({
        id, planKind: 'staged/1', payload: {
            phases: [{
                    id: 'one', activations: [{
                            id, actorId, momentId, driver, source: 'recovery-test', input: null
                        }]
                }], nextCursor: next
        }
    });
    const first = [proposal('seed', 'a', 'seed', 'seed-social', '1'), proposal('perceive', 'c', 'conversation', 'rule', '2')];
    async function install(environment, proposals, seen) {
        const c = environment.ctx;
        await runtime(environment, {
            sourceConfig: {
                proposals
            }
        });
        for (const plugin of [Perceptions, Rankings, Recommendations, SocialPerception])
            await c.plugin(plugin);
        await c.plugin(Rule, {
            handler: 'recovery-reader', sources: ['social/feed']
        });
        await c.plugin({
            inject: ['drivers', 'slots', 'actions'], apply(ctx) {
                ctx.slots.contribute(ruleHandlerSlot, {
                    id: 'recovery-reader', value: {
                        async activate(input) {
                            seen.push(input.threadId);
                            return {
                                status: 'completed', value: null, evidence: {}
                            };
                        }
                    }
                });
                ctx.drivers.register('seed-social', {
                    async activate(_intent, context) {
                        const receipt = await ctx.actions.execute('social.publish', {
                            id: 'durable-post', content: '跨分叉保留的完整原文'
                        }, context);
                        return {
                            status: 'completed', value: null, evidence: {
                                receipt
                            }, requiredOperations: [receipt.operationId]
                        };
                    }
                });
            }
        });
    }
    try {
        const original = [];
        await install(f, first, original);
        const result = await f.ctx.runs.run(new AbortController().signal);
        assert.equal(result.status, 'completed', result.reason);
        assert.equal(original.length, 1);
        const directory = join(dirname(f.directory), 'pr10-perception-fork-' + randomUUID());
        await f.ctx.history.fork({
            operationId: 'perception-fork', pointId: result.cut.id, targetRunId: 'perception-branch', targetDirectory: directory
        });
        branch = await fixture({
            restore: true, directory, runId: 'perception-branch', attemptDirectory: f.attemptDirectory
        });
        const restored = [];
        await install(branch, [...first, proposal('perceive-restored', 'c', 'conversation', 'rule', '3')], restored);
        const resumed = await branch.ctx.runs.run(new AbortController().signal);
        assert.equal(resumed.status, 'completed', resumed.reason);
        assert.deepEqual(restored, original);
        const view = await branch.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        const [row] = await view.query({
            sql: "SELECT (SELECT count(*) FROM perception_material WHERE source_id='social/feed') AS materials,(SELECT count(*) FROM threads) AS threads"
        }, {
            maxRows: 1, maxBytes: 4096
        });
        assert.equal(row.materials, 1n);
        assert.equal(row.threads, 1n);
        await view.close();
    }
    finally {
        await branch?.host.close();
        await f.host.close();
    }
});
test('RV3-09-T01 previewChars bounds only rendered percept and fullRef reads original by public id', async () => {
    const f = await fixture({ migrations: [perceptionMigration({ package: '@society0/core', entry: 'perception' })] });
    let reservation, lease, view;
    try {
        const c = f.ctx;
        for (const plugin of [Perceptions, Rankings, Recommendations, SocialPerception]) await c.plugin(plugin, plugin === SocialPerception ? { previewChars: 300 } : undefined);
        reservation = await c.executionFence.reserve({ id: 'preview' });
        const long = '😀' + 'x'.repeat(999);
        await c.actions.execute('social.publish', { id: 'preview-long', content: long }, activation(f, reservation, 'preview-long', 'a'));
        await c.actions.execute('social.publish', { id: 'preview-short', content: 'short' }, activation(f, reservation, 'preview-short', 'a'));
        view = await c.database.snapshot({ maxAgeMs: 30000 });
        const thread = await c.threads.open('c', 'preview', view);
        const context = activation(f, reservation, 'preview-read', 'c');
        lease = await c.perceptions.acquire(['social/feed'], context);
        const batch = await lease.value.collect({ actorId: 'c', momentId: 'preview', threadId: thread.id, reason: null }, { maxItems: 10, maxResponseBytes: 65536 }, context);
        const item = batch.reads[0].page.items.find(value => value.material.value.id === 'preview-long');
        assert.ok(item, 'long percept present');
        assert.equal(item.material.value.content, '😀' + 'x'.repeat(299));
        assert.equal(item.material.value.truncated, true);
        assert.deepEqual(item.material.value.fullRef, { information: 'social/posts', input: { id: 'preview-long' } });
        const full = await c.information.query('social/posts', { id: 'preview-long' }, { actorId: 'c', momentId: 'preview', query: null }, view, { maxItems: 1, maxResponseBytes: 65536 });
        assert.equal(full.items.length, 1);
        assert.equal(full.items[0].value.id, 'preview-long');
        assert.equal(full.items[0].value.content, long);
        const short = batch.reads[0].page.items.find(value => value.material.value.id === 'preview-short');
        assert.equal(short.material.value.content, 'short');
        assert.equal(short.material.value.truncated, undefined);
        assert.equal(short.material.value.fullRef, undefined);
        const recommendationContext = { ...context, logicalTime: '5', momentId: 'preview' };
        const recommendationsLease = await c.perceptions.acquire(['social/recommendations'], recommendationContext);
        try {
            const recommendations = await recommendationsLease.value.collect({ actorId: 'c', momentId: 'preview', threadId: thread.id, reason: null, logicalTime: '5' }, { maxItems: 10, maxResponseBytes: 65536 }, recommendationContext);
            assert.ok(Array.isArray(recommendations.reads[0].page.items));
        }
        finally {
            await recommendationsLease.release();
        }
    }
    finally {
        await lease?.release();
        await view?.close();
        await reservation?.release();
        await f.host.close();
    }
});
