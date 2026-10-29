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
export const request = operationId => ({ operationId, maxDurationMs: 15000, maxStatements: 1000, maxInputBytes: 1048576, maxChangedRows: 1000, maxCapturedBytes: 1048576 });
export const scope = { actorId: 'a', momentId: 'm', query: null };
export const page = { maxItems: 2, maxResponseBytes: 16384 };
export async function setup({ migrations = [], explorationOptions = {}, threadPlugin = Threads, beforePlain } = {}) {
    const host = await createHost({ boot: [] });
    try {
        const base = await mkdtemp(join(tmpdir(), 'pr07-chain-')), directory = join(base, 'run');
        await host.ctx.plugin(OwnedResources);
        await host.ctx.plugin(TaskSupervisor, { concurrency: 8, maxBytes: 33554432 });
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(ExecutionFence);
        await host.ctx.plugin(SqliteDatabase, { directory, runId: 'pr07', generation: 'g', maxTransactionMs: 20000 });
        await host.ctx.plugin(ArtifactsLocal, { directory: join(directory, 'objects') });
        await host.ctx.plugin(Initializers);
        await host.ctx.plugin(SqliteBootstrap);
        await host.ctx.plugin(SqliteHistory, { directory, runId: 'pr07', generation: 'g', controlSequence: '0' });
        await host.ctx.plugin(Attempts, { directory: join(base, 'attempts'), lineage: 'pr07' });
        await host.ctx.attempts.initialize();
        const source = { consumer: 'pr07-real-chain' };
        await host.ctx.database.applyMigrations([artifactsMigration, actorsMigration, actionsMigration(source), threadMigration(source), resultsMigration(source), perceptionMigration(source), workspaceMigration(source), { owner: 'inventory', from: 0, to: 1, after: [], source, statements: [{ sql: 'CREATE TABLE inventory(id TEXT PRIMARY KEY NOT NULL,quantity INTEGER NOT NULL); INSERT INTO inventory VALUES(\'stock\',1)' }] }, ...migrations]);
        await host.ctx.plugin(Actors);
        await host.ctx.plugin(ActorEligibility);
        await host.ctx.plugin(Actions);
        await host.ctx.plugin(Information);
        await host.ctx.plugin(threadPlugin);
        await host.ctx.plugin(Results);
        await host.ctx.plugin(Perceptions);
        await host.ctx.plugin(Workspace);
        await host.ctx.plugin(Exploration, explorationOptions);
        await host.ctx.plugin(mountWorkspace);
        await host.ctx.plugin(mountResults);
        await host.ctx.plugin(mountWorld);
        await beforePlain?.(host);
        await host.ctx.plugin(plain);
        let ctx;
        await host.ctx.plugin({ inject: ['database', 'executionFence', 'artifacts', 'attempts', 'actors', 'actorEligibility', 'actions', 'information', 'threads', 'results', 'perceptions', 'workspace', 'exploration'], apply(c) { ctx = c; } });
        const reservation = await ctx.executionFence.reserve({ id: 'pr07' });
        await ctx.database.transaction(reservation.lease, request('seed'), async (tx) => {
            for (const id of ['a', 'b']) {
                await ctx.actors.create(tx, { id, attributes: { score: 0, raw: { preserved: true } } });
                await ctx.workspace.initialize(tx, id);
            }
        });
        return { host, ctx, directory, reservation, context: (operationId, actorId = 'a', momentId = 'm') => ({ ctx, database: ctx.database, writeLease: reservation.lease, actorId, momentId, logicalTime: 1, runId: 'pr07', generation: 'g', operationId, signal: reservation.signal }), close: async () => { await reservation.release(); await host.close(); } };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}
export async function read(ctx, body) {
    const view = await ctx.database.snapshot({ maxAgeMs: 30000 });
    try {
        return await body(view);
    }
    finally {
        await view.close();
    }
}
