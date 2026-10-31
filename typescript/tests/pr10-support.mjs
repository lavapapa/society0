import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import CompositionStrategies from '../.build/host/dist/composition-strategies/index.js';
import SlotStrategies from '../.build/host/dist/slot-strategies/index.js';
import Slots from '../.build/host/dist/slots/index.js';
const core = async (name) => import('../.build/core/dist/' + name + '/index.js');
const social = async (name) => import('../.build/social/dist/' + name + '/index.js');
const [Fence, Database, Artifacts, Initializers, Bootstrap, History, Attempts, Actors, Eligibility, Actions, Information, Results, Threads] = await Promise.all(['execution-fence', 'sqlite', 'artifacts-local', 'initializers', 'sqlite-bootstrap', 'sqlite-history', 'attempts', 'actors', 'actor-eligibility', 'actions', 'information', 'results', 'thread'].map(core));
const [Repo, Init, Topology, Social, Neighbors] = await Promise.all(['social-repository', 'social-init', 'topology-edges', 'social', 'propagation-neighbors'].map(social));
const source = {
    package: '@society0/social', version: '0.1.0', entry: 'test'
};
export const request = operationId => ({
    operationId, maxDurationMs: 15000, maxStatements: 1024, maxInputBytes: 1048576, maxChangedRows: 10000, maxCapturedBytes: 4194304
});
export const bounds = {
    maxItems: 2, maxResponseBytes: 65536
};
export async function fixture(options = {}) {
    const host = await createHost({
        boot: []
    });
    const c = host.ctx;
    const directory = options.directory ?? await mkdtemp(join(tmpdir(), 'pr10-'));
    const runId = options.runId ?? 'social';
    const attemptDirectory = options.attemptDirectory ?? await mkdtemp(join(tmpdir(), 'pr10-evidence-'));
    const lineage = options.lineage ?? 'social';
    try {
        for (const plugin of [OwnedResources, TaskSupervisor, CompositionStrategies, SlotStrategies, Slots, Fence.default])
            await c.plugin(plugin);
        await c.plugin(Database.default, {
            directory, runId, generation: 'g', maxTransactionMs: 20000
        });
        await c.plugin(Artifacts.default, {
            directory: join(directory, 'artifacts')
        });
        await c.plugin(Initializers.default);
        await c.plugin(Bootstrap.default, {
            maxTransactionMs: 15000
        });
        await c.plugin(History.default, {
            directory, runId, generation: 'g', controlSequence: '0'
        });
        await c.plugin(Attempts.default, {
            directory: attemptDirectory, lineage, ...(options.attempts ?? {})
        });
        if (options.restore)
            await c.attempts.verifyAuthority();
        else
            await c.attempts.initialize();
        await c.plugin(Actors.default);
        await c.plugin(Eligibility.default);
        await c.plugin(Results.default);
        await c.plugin(Threads.default);
        await c.plugin(Repo.default);
        await c.plugin(Init.default);
        await c.plugin(Topology.default, {
            actors: [{
                    id: 'a', attributes: {
                        name: '甲', interests: ['science']
                    }
                }, {
                    id: 'b', attributes: {
                        name: '乙'
                    }
                }, {
                    id: 'c', attributes: {
                        name: '丙'
                    }
                }], edges: options.edges ?? [{
                    from: 'b', to: 'a'
                }, {
                    from: 'c', to: 'a'
                }]
        });
        const root = options.restore ? await c.history.activate() : await c.sqliteBootstrap.create({
            operationId: 'create', runId, directory, input: {}, migrations: [Artifacts.artifactsMigration, Actors.actorsMigration, Repo.socialMigration, Actions.actionsMigration(source), Results.resultsMigration(source), Threads.threadMigration(source), ...(options.migrations ?? [])], initialFrontier: {
                codec: options.event ? 'events/1' : 'staged/1', value: '0', logicalTime: options.event ? {
                    time: '0', offset: '0'
                } : '0', completedCount: '0', pendingAcknowledgements: []
            }, initialControl: {
                generation: 'g', controlSequence: '0', planId: 'social-plan'
            }, requiredInitializerIds: ['social.seed']
        });
        await c.plugin(Actions.default);
        await c.plugin(Information.default);
        if (options.social !== false) {
            await c.plugin(Social.default, {
                propagation: options.propagation ?? 'neighbor-broadcast/v1', contentLengthLimit: -1
            });
            await c.plugin(Neighbors.default);
        }
        return {
            host, ctx: c, directory, root, runId, attemptDirectory
        };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}
export function activation(f, reservation, operationId, actorId = 'a') {
    return {
        ctx: f.ctx, runId: f.runId, generation: 'g', operationId, actorId, momentId: 'moment', logicalTime: '1', signal: reservation.signal, database: f.ctx.database, writeLease: reservation.lease
    };
}
export async function runtime(f, options = {}) {
    const c = f.ctx;
    for (const name of ['drivers', 'activation-mailbox', 'activation', options.event ? 'clock-event' : 'clock-ticks'])
        await c.plugin((await core(name)).default);
    await c.plugin(options.source ?? (await core(options.event ? 'proposal-events' : 'proposal-sequence')).default, options.sourceConfig ?? (options.event ? {} : {
        proposals: []
    }));
    for (const name of [options.event ? 'runtime-events' : 'runtime-staged', 'completion-strict', 'checkpoint-policy'])
        await c.plugin((await core(name)).default);
    await c.plugin(options.termination ?? (await core('termination')).default);
    await c.plugin((await core('generation-views')).default);
    await c.plugin((await core('generation-binding')).default, {
        generation: 'g'
    });
    await c.plugin((await core('run-controller')).default);
}
