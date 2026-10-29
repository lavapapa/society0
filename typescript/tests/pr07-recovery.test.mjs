import { Initializers } from '../../../evidence/pr07/stage/core/dist/initializers/index.js';
import { SqliteBootstrap } from '../../../evidence/pr07/stage/core/dist/sqlite-bootstrap/index.js';
import { SqliteHistory } from '../../../evidence/pr07/stage/core/dist/sqlite-history/index.js';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { ExecutionFence } from '../../../evidence/pr07/stage/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../../../evidence/pr07/stage/core/dist/sqlite/index.js';
import { ArtifactsLocal, artifactsMigration } from '../../../evidence/pr07/stage/core/dist/artifacts-local/index.js';
import { Attempts } from '../../../evidence/pr07/stage/core/dist/attempts/index.js';
import { Actors, actorsMigration } from '../../../evidence/pr07/stage/core/dist/actors/index.js';
import ActorEligibility from '../../../evidence/pr07/stage/core/dist/actor-eligibility/index.js';
import { Actions, actionsMigration } from '../../../evidence/pr07/stage/core/dist/actions/index.js';
import { Information } from '../../../evidence/pr07/stage/core/dist/information/index.js';
import { Threads, threadMigration } from '../../../evidence/pr07/stage/core/dist/thread/index.js';
import { Results, resultsMigration } from '../../../evidence/pr07/stage/core/dist/results/index.js';
import { Perceptions, perceptionMigration } from '../../../evidence/pr07/stage/core/dist/perception/index.js';
import { Exploration } from '../../../evidence/pr07/stage/core/dist/exploration/index.js';
import { Workspace, workspaceMigration } from '../../../evidence/pr07/stage/extras/dist/workspace/index.js';
import mountWorkspace from '../../../evidence/pr07/stage/core/dist/mount-workspace/index.js';
import mountResults from '../../../evidence/pr07/stage/core/dist/mount-results/index.js';
import mountWorld from '../../../evidence/pr07/stage/core/dist/mount-world/index.js';
import plain from '../../../evidence/pr07/stage/core/dist/plain/index.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import PlainInit from '../../../evidence/pr07/stage/core/dist/plain-init/index.js';
const limits = { maxItems: 10, maxResponseBytes: 16384 }, req = operationId => ({ operationId, maxDurationMs: 20000, maxStatements: 1000, maxInputBytes: 1048576, maxChangedRows: 1000, maxCapturedBytes: 4194304 });
const frontier = n => ({ codec: 'step/1', value: n, logicalTime: n, completedCount: String(n), pendingAcknowledgements: [] });
async function fixture(directory, runId) {
    const host = await createHost({ boot: [] }), ctx = host.ctx;
    try {
        await ctx.plugin(OwnedResources);
        await ctx.plugin(TaskSupervisor, { concurrency: 8, maxBytes: 33554432 });
        await ctx.plugin(CompositionStrategies);
        await ctx.plugin(strategies);
        await ctx.plugin(Slots);
        await ctx.plugin(ExecutionFence);
        await ctx.plugin(ArtifactsLocal, { directory: join(directory, 'artifacts') });
        await ctx.plugin(SqliteDatabase, { directory, runId, generation: 'g', maxTransactionMs: 30000 });
        await ctx.plugin(Initializers);
        await ctx.plugin(SqliteBootstrap, { maxTransactionMs: 20000 });
        await ctx.plugin(SqliteHistory, { directory, runId, generation: 'g', controlSequence: '0' });
        await ctx.plugin(Actors);
        await ctx.plugin(Threads);
        await ctx.plugin(Perceptions);
        ctx.perceptions.register({ id: 'inbox/v1', cursorScope: 'actor', available: async () => true, versions: async () => [{ owner: 'inbox/v1', version: '1' }], collect: async (scope, before) => ({ items: before === null ? [{ id: 'raw-1', kind: 'observation', material: { kind: 'inline', value: { raw: '完整原文' } }, provenance: { source: 'inbox/v1' } }] : [], nextCursor: 1, through: 1, hasMore: false, responseBytes: 0 }) });
        return { host, ctx, directory, runId };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}
async function opened(ctx) {
    const view = await ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
        return await ctx.threads.open('a', 'm', view);
    }
    finally {
        await view.close();
    }
}
test('PR07 actual complete-point reopening and fork preserve stable perception cursor and opaque Thread', async (t) => {
    const home = await mkdtemp(join(tmpdir(), 'pr07-recovery-')), directory = join(home, 'source'), target = join(home, 'fork'), source = await fixture(directory, 'source');
    let thread, phase = "create";
    try {
        await source.ctx.plugin(PlainInit, { initialActors: [{ id: 'a', attributes: { original: true } }] });
        const identity = { consumer: 'pr07-recovery.test.mjs' }, root = await source.ctx.sqliteBootstrap.create({ operationId: 'create', runId: 'source', directory, input: {}, migrations: [artifactsMigration, actorsMigration, threadMigration(identity), perceptionMigration(identity)], initialFrontier: frontier(0), initialControl: { generation: 'g', controlSequence: '0', planId: 'pr07-recovery' }, requiredInitializerIds: ['plain.seed'] });
        phase = "capture";
        thread = await opened(source.ctx);
        const task = source.ctx.tasks.run('capture', async (signal) => {
            const reservation = await source.ctx.executionFence.reserve({ id: 'capture' }), context = { runId: 'source', generation: 'g', operationId: 'capture', signal }, selected = await source.ctx.perceptions.acquire(['inbox/v1'], context);
            try {
                const scope = { actorId: 'a', momentId: 'm', threadId: thread.id, logicalTime: 0 }, batch = await selected.value.collect(scope, limits, context), message = await source.ctx.threads.prepare({ id: 'message-1', interactionId: thread.id, role: 'user', content: '完整原文' });
                await source.ctx.database.transaction(reservation.lease, req('capture'), async (tx) => { await selected.value.commit(tx, batch, [{ sourceId: 'inbox/v1', perceptIds: ['raw-1'], message, contexts: [] }]); await source.ctx.database.metadata.complete(tx, frontier(1), []); });
            }
            finally {
                await selected.release();
                await reservation.release();
            }
        });
        source.ctx.history.bindQuiescence(() => task.settled);
        await task;
        await task.settled;
        const lease = await source.ctx.executionFence.seal(), point = await source.ctx.history.publish(lease, { operationId: 'publish', id: 'captured', parentId: root.id, expectedRevision: await source.ctx.database.readRevision(), requiredOperations: ['capture'] });
        phase = 'fork';
        await source.ctx.history.fork({ operationId: 'fork', pointId: point.id, targetRunId: 'fork', targetDirectory: target });
    }
    catch (error) {
        console.error({ phase, error, diagnostics: source.ctx.database.diagnostics() });
        throw error;
    }
    finally {
        await source.host.close();
    }
    for (const [name, path, runId] of [['fresh reopen', directory, 'source'], ['actual history fork', target, 'fork']])
        await t.test(name, async () => {
            const reopened = await fixture(path, runId);
            let selected;
            try {
                assert.equal((await reopened.ctx.history.status()).state, 'clean');
                await reopened.ctx.history.activate();
                const current = await opened(reopened.ctx);
                assert.equal(current.id, thread.id);
                assert.equal(current.messageCount, '1');
                const context = { runId, generation: 'g', operationId: 'resume', signal: new AbortController().signal };
                selected = await reopened.ctx.perceptions.acquire(['inbox/v1'], context);
                const batch = await selected.value.collect({ actorId: 'a', momentId: 'm', threadId: current.id, logicalTime: 1 }, limits, context);
                assert.equal(batch.reads[0].before, 1);
                assert.equal(batch.reads[0].page.items.length, 0);
                const view = await reopened.ctx.database.snapshot({ maxAgeMs: 30000 });
                try {
                    assert.equal((await reopened.ctx.threads.page(current.id, { actorId: 'a', momentId: 'm', query: null }, view, limits)).total, '1');
                }
                finally {
                    await view.close();
                }
            }
            finally {
                await selected?.release();
                await reopened.host.close();
            }
        });
});
