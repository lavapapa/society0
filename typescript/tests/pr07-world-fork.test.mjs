const ActionEntry = await import(process.env.PR07_ACTIONS_ENTRY ?? '../../../evidence/pr07/stage/core/dist/actions/index.js');
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
async function fixture(directory, runId, authority, initialize = false) {
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
        await ctx.plugin(Attempts, { directory: authority, lineage: "world-lineage" });
        if (initialize)
            await ctx.attempts.initialize();
        else
            await ctx.attempts.verifyAuthority();
        await ctx.plugin(Actors);
        await ctx.plugin(ActorEligibility);
        await ctx.plugin(ActionEntry.Actions);
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
async function act(env) {
    const { ctx, runId } = env;
    ctx.actions.register({ name: 'world.bump', inputSchema: {}, outputSchema: {}, effect: 'world', available: async () => true, async execute(input, context) { const result = await context.transaction(req('same-world-write'), tx => ctx.actors.update(tx, { id: 'a', attributes: { replayed: true } })); return { operationId: 'same-world-write', status: 'completed', value: result.value, original: { world: true }, artifacts: [] }; } });
    const reservation = await ctx.executionFence.reserve({ id: 'action' });
    try {
        return await ctx.actions.execute('world.bump', null, { ctx, actorId: 'a', momentId: 'm', logicalTime: 1, runId, generation: 'g', operationId: 'same-invocation', database: ctx.database, writeLease: reservation.lease, signal: reservation.signal });
    }
    finally {
        await reservation.release();
    }
}
test('PR07 a world action after the parent point replays in a true fork with the same lineage and logical operation', async () => {
    const home = await mkdtemp(join(tmpdir(), 'pr07-world-fork-')), directory = join(home, 'source'), target = join(home, 'fork'), authority = join(home, 'authority'), source = await fixture(directory, 'source', authority, true);
    let branch;
    try {
        await source.ctx.plugin(PlainInit, { initialActors: [{ id: 'a', attributes: { initial: true } }] });
        const identity = { consumer: 'pr07-world-fork.test.mjs' }, root = await source.ctx.sqliteBootstrap.create({ operationId: 'create', runId: 'source', directory, input: {}, migrations: [artifactsMigration, actorsMigration, ActionEntry.actionsMigration(identity), threadMigration(identity), perceptionMigration(identity)], initialFrontier: frontier(0), initialControl: { generation: 'g', controlSequence: '0', planId: 'world-fork' }, requiredInitializerIds: ['plain.seed'] });
        assert.equal((await act(source)).status, 'completed');
        await source.ctx.history.fork({ operationId: 'fork-before-action', pointId: root.id, targetRunId: 'fork', targetDirectory: target });
        await source.host.close();
        branch = await fixture(target, 'fork', authority);
        await branch.ctx.history.activate();
        assert.equal((await act(branch)).status, 'completed');
        const view = await branch.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            assert.deepEqual((await branch.ctx.actors.get('a', view)).attributes, { replayed: true });
        }
        finally {
            await view.close();
        }
    }
    finally {
        await branch?.host.close();
        await source.host.close();
    }
});
