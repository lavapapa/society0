import { Initializers } from '../.build/core/dist/initializers/index.js';
import { SqliteBootstrap } from '../.build/core/dist/sqlite-bootstrap/index.js';
import { SqliteHistory } from '../.build/core/dist/sqlite-history/index.js';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { ExecutionFence } from '../.build/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../.build/core/dist/sqlite/index.js';
import { ArtifactsLocal, artifactsMigration } from '../.build/core/dist/artifacts-local/index.js';
import { Attempts } from '../.build/core/dist/attempts/index.js';
import { Actors, actorsMigration } from '../.build/core/dist/actors/index.js';
import ActorEligibility from '../.build/core/dist/actor-eligibility/index.js';
import { Actions, actionsMigration } from '../.build/core/dist/actions/index.js';
import { Information } from '../.build/core/dist/information/index.js';
import { Threads, threadMigration } from '../.build/core/dist/thread/index.js';
import { Results, resultsMigration } from '../.build/core/dist/results/index.js';
import { Perceptions, perceptionMigration } from '../.build/core/dist/perception/index.js';
import { Exploration } from '../.build/core/dist/exploration/index.js';
import { Workspace, workspaceMigration } from '../.build/extras/dist/workspace/index.js';
import mountWorkspace from '../.build/core/dist/mount-workspace/index.js';
import mountResults from '../.build/core/dist/mount-results/index.js';
import mountWorld from '../.build/core/dist/mount-world/index.js';
import plain from '../.build/core/dist/plain/index.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import PlainInit from '../.build/core/dist/plain-init/index.js';
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
const directory = await mkdtemp(join(tmpdir(), 'pr07-bare-root-')), env = await fixture(directory, 'bare-root');
try {
    console.log(await env.ctx.sqliteBootstrap.create({ operationId: 'bare-create', runId: 'bare-root', directory, input: {}, migrations: [artifactsMigration], initialFrontier: frontier(0), initialControl: { generation: 'g', controlSequence: '0', planId: 'bare-root' }, requiredInitializerIds: [] }));
}
catch (error) {
    console.error({ directory, error, diagnostics: env.ctx.database.diagnostics() });
    throw error;
}
finally {
    await env.host.close();
}
