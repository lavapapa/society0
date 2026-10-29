import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { fixture, activation, request } from './pr10-support.mjs';
import Rankings from '../../../evidence/pr10/stage/social/dist/social-ranking/index.js';
import Recommendations from '../../../evidence/pr10/stage/social/dist/social-recommendations/index.js';
import Comparison from '../../../evidence/pr10/stage/social/dist/social-measurement/index.js';
const bounds = {
    maxItems: 10, maxBytes: 65536
}, page = {
    maxItems: 10, maxResponseBytes: 131072
};
test('PR10-T07/T09/T10 real HTTP preparation, relevant-version cache, score comparison without presentation', async () => {
    const entered = Promise.withResolvers(), release = Promise.withResolvers();
    let block = false, calls = 0;
    const server = createServer(async (req, res) => {
        calls++;
        for await (const chunk of req) {
        }
        if (block) {
            entered.resolve();
            await release.promise;
        }
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({
            score: 0.8, original: {
                fixture: 'third-party-scoring'
            }
        }));
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const url = 'http://127.0.0.1:' + server.address().port;
    const f = await fixture({
        social: false
    });
    let r;
    try {
        const c = f.ctx;
        await c.plugin(Rankings, {
            timeDecay: 2000000
        });
        await c.plugin(Recommendations, {
            sources: ['social/posts/v1', 'fixture/scoring']
        });
        await c.plugin(Comparison);
        await c.plugin({
            name: 'http-scoring-source', inject: ['recommendationSources'], apply(ctx) {
                ctx.recommendationSources.register({
                    id: 'fixture/scoring', version: 1, dependencies: ['social/preferences', 'third-party/actor-count'], versions: async (view) => {
                        const [row] = await view.query({
                            sql: 'SELECT count(*) AS n FROM s0_actors'
                        }, {
                            maxRows: 1, maxBytes: 1024
                        });
                        return [...await c.socialRepository.versions(view, ['social/preferences']), {
                                owner: 'third-party/actor-count', version: String(row.n)
                            }];
                    }, async collect(scope, view) {
                        return {
                            items: [], total: '0', revision: view.revision, responseBytes: 100
                        };
                    }, async prepare(input, context) {
                        assert.equal(c.database.diagnostics().snapshots, 0);
                        const original = await (await fetch(url, {
                            method: 'POST', body: JSON.stringify(input), signal: context.signal
                        })).json();
                        return {
                            status: 'ready', input: {
                                ...input, items: input.items.map(item => ({
                                    ...item, features: {
                                        ...item.features, similarity: original.score, semanticOriginal: original
                                    }
                                }))
                            }
                        };
                    }
                });
            }
        });
        r = await c.executionFence.reserve({
            id: 'recommendations'
        });
        await c.database.transaction(r.lease, request('seed-posts'), async (tx) => {
            await c.socialRepository.publish(tx, {
                id: 'old', authorId: 'a', content: 'Older original', tags: [], createdTime: '9007199254740993'
            }, ['b']);
            await c.socialRepository.publish(tx, {
                id: 'new', authorId: 'c', content: 'Newer original', tags: [], createdTime: '9007199254740994'
            }, ['b']);
            await c.socialRepository.react(tx, {
                id: 'like', actorId: 'b', postId: 'old', kind: 'like', time: '9007199254740995'
            });
        });
        const scope = {
            actorId: 'b', momentId: 'm', query: {
                time: '9007199254740995'
            }
        }, context = activation(f, r, 'prepare', 'b');
        const prepared = await c.recommendations.prepare(scope, bounds, context);
        assert.equal(prepared.status, 'ready');
        assert.equal(prepared.input.items[0].features.semanticOriginal.original.fixture, 'third-party-scoring');
        assert.equal(calls, 1);
        const chronological = await c.recommendations.preview(prepared.input, 'chronological/v1', page), weighted = await c.recommendations.preview(prepared.input, 'weighted/v1', page);
        assert.equal(chronological.items[0].id, 'new');
        assert.equal(weighted.items[0].id, 'old');
        assert.equal(weighted.items[0].components.semanticScore, 0.8);
        const result = await c.recommendationMeasurement.compare(prepared.input, ['chronological/v1', 'weighted/v1'], r.lease, {
            ...context, operationId: 'compare'
        });
        assert.equal((await c.database.probe(result.requiredOperations[0])).status, 'committed');
        const view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal((await c.socialRepository.get('old', view)).views, '0');
        assert.equal(String((await view.query({
            sql: 'SELECT count(*) AS n FROM social_presentations'
        }, {
            maxRows: 1, maxBytes: 4096
        }))[0].n), '0');
        await view.close();
        await c.database.transaction(r.lease, request('unrelated'), tx => tx.execute({
            sql: 'UPDATE social_notifications SET consumed=consumed'
        }));
        assert.equal((await c.recommendations.prepare(scope, bounds, context)).input.id, prepared.input.id);
        assert.equal(calls, 1);
        await c.database.transaction(r.lease, request('prefs'), tx => c.socialRepository.setPreferences(tx, 'b', {
            topic: 'changed'
        }));
        block = true;
        const pending = c.recommendations.prepare(scope, bounds, context);
        await entered.promise;
        assert.equal(c.database.diagnostics().snapshots, 0);
        await c.database.transaction(r.lease, request('race-prefs'), tx => c.socialRepository.setPreferences(tx, 'b', {
            topic: 'changed-during-http'
        }));
        release.resolve();
        const stale = await pending;
        assert.equal(stale.status, 'stale');
        assert.ok(stale.changed.includes('social/preferences'));
        block = false;
        const final = await c.recommendations.prepare(scope, bounds, context);
        assert.equal(final.status, 'ready');
        const presentation = {
            presentationId: 'show', actorId: 'b', momentId: 'm', inputId: final.input.id, versions: final.input.versions, itemIds: ['old']
        };
        await c.database.transaction(r.lease, request('show'), tx => c.socialRepository.present(tx, presentation));
        await c.database.transaction(r.lease, request('show-again'), tx => c.socialRepository.present(tx, presentation));
        const read = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal((await c.socialRepository.get('old', read)).views, '1');
        assert.equal((await c.socialRepository.get('new', read)).views, '0');
        await read.close();
        await c.database.transaction(r.lease, request('foreign-domain-change'), tx => c.actors.create(tx, {
            id: 'independent-actor', attributes: {}
        }));
        await assert.rejects(c.database.transaction(r.lease, request('stale-foreign-presentation'), tx => c.socialRepository.present(tx, {
            ...presentation, presentationId: 'stale-foreign'
        })), /SOCIAL_STALE.*third-party\/actor-count/);
        await c.database.transaction(r.lease, request('replay-after-domain-change'), tx => c.socialRepository.present(tx, presentation));
        const refreshed = await c.recommendations.prepare(scope, bounds, context);
        assert.equal(refreshed.status, 'ready');
        assert.notEqual(refreshed.input.id, final.input.id);
        assert.equal(refreshed.input.versions.find(x => x.owner === 'third-party/actor-count').version, '4');
        const distant = await c.recommendations.prepare({
            ...scope, momentId: 'long-decay', query: {
                time: '9007199256740993'
            }
        }, bounds, context);
        assert.equal(distant.status, 'ready');
        const distantRank = await c.recommendations.preview(distant.input, 'weighted/v1', page);
        assert.equal(distantRank.items.find(item => item.id === 'old').components.timeScore, Math.exp(-1));
        assert.ok(c.recommendations.diagnostics().cacheBytes <= 4194304);
    }
    finally {
        release.resolve();
        await r?.release();
        await f.host.close();
        server.close();
        await once(server, 'close');
    }
});
