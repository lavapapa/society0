import Embeddings from '../.build/core/dist/embedding/index.js';
import EmbeddingHttp from '../.build/core/dist/embedding-http/index.js';
import Credentials from '../.build/core/dist/model-credentials/index.js';
import Rankings from '../.build/social/dist/social-ranking/index.js';
import Recommendations from '../.build/social/dist/social-recommendations/index.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { fixture, activation, request } from './pr10-support.mjs';
import Providers from '../.build/extras/dist/vector-providers/index.js';
import Exact, { ExactMemoryIndex } from '../.build/extras/dist/vector-exact/index.js';
import Semantic, { SemanticProjection, semanticMigration } from '../.build/social/dist/social-semantic/index.js';
test('PR10-T08 actual exact index survives apply/progress crash and fresh projection replays saved vectors', async () => {
    let calls = 0;
    const server = createServer(async (req, res) => {
        calls++;
        let body = '';
        for await (const chunk of req)
            body += chunk;
        const input = JSON.parse(body);
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({
            items: input.texts.map((text, itemIndex) => ({
                itemIndex, vector: itemIndex === 0 ? [1, 0] : [0, 1], originalText: text
            })), usage: {
                requests: 1
            }, provider: 'deterministic-http-fixture'
        }));
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const f = await fixture({
        social: false, migrations: [semanticMigration]
    });
    let r, held;
    try {
        const c = f.ctx;
        await c.plugin(Providers, {
            runId: f.runId
        });
        const identity = {
            provider: 'fixture-http', configId: 'fixture/v1', model: 'deterministic-vector', dimensions: 2
        };
        const config = {
            name: 'social-index', generation: 'g', space: 'social', identity, distance: 'cosine', maxItems: 100, maxBytes: 1048576, maxBatchItems: 64, maxQueryItems: 100
        };
        let crash = true;
        class CrashAfterApply extends ExactMemoryIndex {
            async apply(changes, signal) {
                await super.apply(changes, signal);
                if (crash) {
                    crash = false;
                    r.cancel(Error('crash after physical apply'));
                }
            }
        }
        const index = new CrashAfterApply({
            ...config, runId: f.runId
        });
        const owner = await c.plugin({
            inject: ['vectorProviders'], apply(ctx) {
                ctx.vectorProviders.register('social-index', index);
            }
        });
        const projection = new SemanticProjection(c, 'social-index', 'social', 64);
        await c.plugin({
            inject: ['socialRepository'], apply(ctx) {
                ctx.socialRepository.onContent((tx, post) => projection.enqueue(tx, post));
            }
        });
        r = await c.executionFence.reserve({
            id: 'semantic'
        });
        await c.database.transaction(r.lease, request('posts'), async (tx) => {
            await c.socialRepository.publish(tx, {
                id: 'a-post', authorId: 'a', content: 'First original', createdTime: '1'
            }, []);
            await c.socialRepository.publish(tx, {
                id: 'b-post', authorId: 'b', content: 'Second original', createdTime: '1'
            }, []);
        });
        const attempt = {
            lineage: c.attempts.authority().lineage, operationId: 'actual-http-embedding', attempt: 0
        };
        assert.equal((await c.attempts.claim(attempt, {
            texts: ['First original', 'Second original']
        })).status, 'send-permitted');
        const raw = await (await fetch('http://127.0.0.1:' + server.address().port, {
            method: 'POST', body: JSON.stringify({
                texts: ['First original', 'Second original']
            })
        })).json();
        await c.attempts.settle(attempt, raw);
        const expectedRevision = await c.database.readRevision();
        const revision = String(BigInt(expectedRevision) + 1n);
        await c.database.transaction(r.lease, {
            ...request('save-vectors'), expectedRevision
        }, async (tx) => {
            for (const [i, id] of ['a-post', 'b-post'].entries())
                await projection.save(tx, id, '1', {
                    identity, vector: raw.items[i].vector, origin: {
                        attempt, itemIndex: i
                    }
                }, revision);
        });
        await assert.rejects(projection.sync(r.lease, activation(f, r, 'sync-crash')), /crash|FENCE|abort|cancel/i);
        assert.equal(index.diagnostics().items, 2);
        assert.equal(index.diagnostics().after.id, 'b-post');
        let view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal(await projection.progress(view, index), undefined);
        assert.equal((await view.query({
            sql: 'SELECT count(*) AS n FROM social_semantic_pending'
        }, {
            maxRows: 1, maxBytes: 1024
        }))[0].n, 0n);
        await view.close();
        await r.release();
        r = await c.executionFence.reserve({
            id: 'retry'
        });
        await projection.sync(r.lease, activation(f, r, 'retry'));
        view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        let progress = await projection.progress(view, index);
        await view.close();
        assert.equal((await index.query([1, 0], 2, progress, r.signal))[0].id, 'a-post');
        assert.equal(calls, 1);
        await owner.dispose();
        await c.plugin(Exact, config);
        held = await c.vectorProviders.acquire('social-index');
        assert.equal(held.value.diagnostics().items, 0);
        await held.release();
        held = undefined;
        await projection.sync(r.lease, activation(f, r, 'restart-replay'));
        held = await c.vectorProviders.acquire('social-index');
        view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        progress = await projection.progress(view, held.value);
        const saved = await view.query({
            sql: 'SELECT origin,vector FROM social_semantic_vectors ORDER BY post_id'
        }, {
            maxRows: 2, maxBytes: 65536
        });
        await view.close();
        assert.equal((await held.value.query([0, 1], 2, progress, r.signal))[0].id, 'b-post');
        assert.equal(JSON.parse(saved[0].origin).attempt.operationId, 'actual-http-embedding');
        assert.deepEqual(JSON.parse(saved[1].vector), [0, 1]);
        assert.equal(calls, 1);
        await c.database.transaction(r.lease, request('edit-text'), tx => c.socialRepository.revise(tx, 'a-post', 'Changed full original', []));
        const next = String(BigInt(await c.database.readRevision()) + 1n);
        await assert.rejects(c.database.transaction(r.lease, request('stale-vector'), tx => projection.save(tx, 'a-post', '1', {
            identity, vector: [1, 0], origin: {
                attempt, itemIndex: 0
            }
        }, next)), /SOCIAL_SEMANTIC_TEXT_STALE/);
        assert.equal((await c.attempts.probe(attempt)).record.result.items[0].originalText, 'First original');
    }
    finally {
        await held?.release();
        await r?.release();
        await f.host.close();
        server.close();
        await once(server, 'close');
    }
});
test('PR10-T08/T09 full actual Embeddings HTTP service feeds SQL semantic preparation and recreated index', async () => {
    const physical = [], snapshots = [];
    let f, r;
    const server = createServer(async (req, res) => {
        let body = '';
        for await (const chunk of req)
            body += chunk;
        const input = JSON.parse(body).input;
        physical.push(input);
        snapshots.push(f.ctx.database.diagnostics().snapshots);
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({
            data: input.map((text, index) => ({
                index, embedding: text.startsWith('Alpha') || text === '{}' ? [1, 0] : [0, 1], originalText: text
            })), usage: {
                tokens: input.length
            }, vendorOpaque: {
                preserved: '完整提供者响应'
            }
        }));
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    try {
        f = await fixture({
            social: false, migrations: [semanticMigration]
        });
        const c = f.ctx, identity = {
            provider: 'social-http', configId: 'fixture/v1', model: 'fixture-vector', dimensions: 2
        };
        await c.plugin(Embeddings);
        await c.plugin(Credentials);
        await c.plugin(EmbeddingHttp, {
            ...identity, url: 'http://127.0.0.1:' + server.address().port + '/embeddings', limits: {
                maxWaitMs: 1
            }
        });
        await c.plugin(Providers, {
            runId: f.runId
        });
        const config = {
            name: 'social-index', generation: 'g', space: 'social', identity, distance: 'cosine', maxItems: 100, maxBytes: 1048576, maxBatchItems: 64, maxQueryItems: 100
        };
        let owner = await c.plugin(Exact, config);
        await c.plugin(Rankings, {
            similarityWeight: 10
        });
        await c.plugin(Recommendations, {
            sources: ['social/posts/v1', 'social/semantic/v1']
        });
        await c.plugin(Semantic, {
            embeddingProvider: 'social-http', vectorProvider: 'social-index'
        });
        r = await c.executionFence.reserve({
            id: 'semantic-full'
        });
        await c.database.transaction(r.lease, request('semantic-posts'), async (tx) => {
            await c.socialRepository.publish(tx, {
                id: 'alpha', authorId: 'a', content: 'Alpha full original', createdTime: '1'
            }, []);
            await c.socialRepository.publish(tx, {
                id: 'beta', authorId: 'b', content: 'Beta full original', createdTime: '1'
            }, []);
        });
        let view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal((await view.query({
            sql: 'SELECT count(*) AS n FROM social_semantic_pending'
        }, {
            maxRows: 1, maxBytes: 1024
        }))[0].n, 2n);
        await view.close();
        const context = activation(f, r, 'semantic-embed', 'c'), embedded = await c.socialSemantic.embedPending(r.lease, context);
        assert.equal(embedded.status, 'ready');
        assert.equal((await c.database.probe(embedded.requiredOperations[0])).status, 'committed');
        assert.deepEqual(physical, [['Alpha full original', 'Beta full original']]);
        await assert.rejects(c.socialSemantic.embedPending(r.lease, {
            ...context, runId: 'foreign-run'
        }), /SOCIAL_SEMANTIC_RUN/);
        await c.socialSemantic.syncIndex(r.lease, {
            ...context, operationId: 'semantic-index'
        });
        const scope = {
            actorId: 'c', momentId: 'm', query: {
                time: '1'
            }
        }, bounds = {
            maxItems: 10, maxBytes: 65536
        };
        const prepared = await c.recommendations.prepare(scope, bounds, {
            ...context, operationId: 'semantic-prepare'
        });
        assert.equal(prepared.status, 'ready');
        assert.equal(physical.length, 2);
        const ranked = await c.recommendations.preview(prepared.input, 'weighted/v1', {
            maxItems: 10, maxResponseBytes: 65536
        });
        assert.equal(ranked.items[0].id, 'alpha');
        assert.equal(ranked.items[0].components.semanticScore, 1);
        view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        const saved = await view.query({
            sql: 'SELECT origin,vector FROM social_semantic_vectors ORDER BY post_id'
        }, {
            maxRows: 2, maxBytes: 65536
        });
        assert.equal((await view.query({
            sql: 'SELECT count(*) AS n FROM social_semantic_pending'
        }, {
            maxRows: 1, maxBytes: 1024
        }))[0].n, 0n);
        await view.close();
        const origin = JSON.parse(saved[1].origin), record = (await c.attempts.probe(origin.attempt)).record;
        assert.equal(origin.itemIndex, 1);
        assert.equal(record.status, 'settled');
        assert.equal(record.result.usage.tokens, 2);
        let raw = '';
        for await (const chunk of c.attempts.readArtifact(record.result.original))
            raw += Buffer.from(chunk);
        assert.equal(JSON.parse(raw).vendorOpaque.preserved, '完整提供者响应');
        assert.equal(JSON.parse(raw).data[1].originalText, 'Beta full original');
        await owner.dispose();
        owner = await c.plugin(Exact, {
            ...config, identity: {
                ...identity, configId: 'different-vector-config'
            }
        });
        await assert.rejects(c.socialSemantic.prepare(prepared.input, context), /SOCIAL_SEMANTIC_IDENTITY/);
        const incompatible = await c.vectorProviders.acquire('social-index');
        view = await c.database.snapshot({
            maxAgeMs: 30000
        });
        assert.equal(await c.socialSemantic.projection.progress(view, incompatible.value), undefined);
        await view.close();
        await incompatible.release();
        assert.equal(physical.length, 2);
        await owner.dispose();
        owner = await c.plugin(Exact, config);
        await c.socialSemantic.syncIndex(r.lease, {
            ...context, operationId: 'semantic-recreate'
        });
        assert.equal(physical.length, 2);
        await c.database.transaction(r.lease, request('semantic-revise'), tx => c.socialRepository.revise(tx, 'alpha', 'Alpha revised full original', []));
        const stale = await c.recommendations.prepare(scope, bounds, {
            ...context, operationId: 'semantic-stale'
        });
        assert.equal(stale.status, 'stale');
        assert.equal(physical.length, 2);
        await c.socialSemantic.embedPending(r.lease, {
            ...context, operationId: 'semantic-reembed'
        });
        await c.socialSemantic.syncIndex(r.lease, {
            ...context, operationId: 'semantic-reindex'
        });
        const current = await c.recommendations.prepare(scope, bounds, {
            ...context, operationId: 'semantic-current'
        });
        assert.equal(current.status, 'ready');
        assert.equal(physical.length, 3);
        assert.ok(snapshots.every(count => count === 0));
        await c.database.transaction(r.lease, request('semantic-present'), tx => c.socialRepository.present(tx, {
            presentationId: 'semantic-show', actorId: 'c', momentId: 'm', inputId: current.input.id, versions: current.input.versions, itemIds: ['alpha']
        }));
    }
    finally {
        await r?.release();
        await f?.host.close();
        server.close();
        await once(server, 'close');
    }
});
