// C0/C9 共用装配：真实核心插件 + 可选 metrics 插件，50 tick 规则或扫描工作负载。
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import CompositionStrategies from '../.build/host/dist/composition-strategies/index.js';
import SlotStrategies from '../.build/host/dist/slot-strategies/index.js';
import Slots from '../.build/host/dist/slots/index.js';
import ExecutionFence from '../.build/core/dist/execution-fence/index.js';
import SqliteDatabase from '../.build/core/dist/sqlite/index.js';
import ArtifactsLocal, { artifactsMigration } from '../.build/core/dist/artifacts-local/index.js';
import Initializers from '../.build/core/dist/initializers/index.js';
import SqliteBootstrap from '../.build/core/dist/sqlite-bootstrap/index.js';
import SqliteHistory from '../.build/core/dist/sqlite-history/index.js';
import Actors, { actorsMigration } from '../.build/core/dist/actors/index.js';
import ActorEligibility from '../.build/core/dist/actor-eligibility/index.js';
import Drivers from '../.build/core/dist/drivers/index.js';
import ActivationMailbox from '../.build/core/dist/activation-mailbox/index.js';
import Activation from '../.build/core/dist/activation/index.js';
import ClockTicks from '../.build/core/dist/clock-ticks/index.js';
import ProposalSequence from '../.build/core/dist/proposal-sequence/index.js';
import RuntimeStaged from '../.build/core/dist/runtime-staged/index.js';
import CompletionStrict from '../.build/core/dist/completion-strict/index.js';
import CheckpointPolicy from '../.build/core/dist/checkpoint-policy/index.js';
import Termination from '../.build/core/dist/termination/index.js';
import GenerationViews from '../.build/core/dist/generation-views/index.js';
import GenerationBinding from '../.build/core/dist/generation-binding/index.js';
import RunController from '../.build/core/dist/run-controller/index.js';
import AgentRuntimes from '../.build/core/dist/agent-runtimes/index.js';
import DeferredWork, { deferredWorkMigration } from '../.build/core/dist/deferred-work/index.js';

export const req = operationId => ({
  operationId, maxDurationMs: 30000, maxStatements: 1000, maxInputBytes: 1048576,
  maxChangedRows: 10000, maxCapturedBytes: 4194304
});
export const curveMigration = {
  owner: 'metrics-test', from: 0, to: 1, after: [], source: { consumer: 'metrics-test' },
  statements: [{ sql: 'CREATE TABLE curve(id INTEGER PRIMARY KEY NOT NULL,v INTEGER NOT NULL);' }]
};

async function metricsPlugin() {
  try { return (await import('../.build/core/dist/metrics/index.js')).default; }
  catch { return undefined; }
}

export function proposals(ticks, actorIds) {
  return Array.from({ length: ticks }, (_, tick) => ({
    id: 'p' + tick,
    planKind: 'staged/1',
    payload: {
      phases: [{ id: 'act', activations: actorIds.map((actorId, index) => ({
        id: 'a' + tick + '-' + index, actorId, momentId: 'm' + tick, source: 'agenda', driver: 'rule', input: { tick }
      })) }],
      nextCursor: String(tick + 1)
    }
  }));
}

export async function fixture(options = {}) {
  const host = await createHost({ boot: [] });
  const ctx = host.ctx;
  const directory = options.directory ?? await mkdtemp(join(tmpdir(), 'metrics-run-'));
  const runId = options.runId ?? 'metrics';
  const actors = options.actors ?? ['a'];
  const ticks = options.ticks ?? 3;
  try {
    for (const plugin of [OwnedResources, TaskSupervisor, CompositionStrategies, SlotStrategies, Slots, ExecutionFence]) await ctx.plugin(plugin);
    await ctx.plugin(SqliteDatabase, { directory, runId, generation: 'g', maxTransactionMs: 30000 });
    await ctx.plugin(ArtifactsLocal, { directory: join(directory, 'artifacts') });
    await ctx.plugin(Initializers);
    await ctx.plugin(SqliteBootstrap, { maxTransactionMs: 20000 });
    await ctx.plugin(SqliteHistory, { directory, runId, generation: 'g', controlSequence: '0' });
    await ctx.plugin(Actors);
    await ctx.plugin(ActorEligibility);
    await ctx.plugin(Drivers);
    await ctx.plugin(ActivationMailbox);
    await ctx.plugin(Activation, options.activationConfig ?? { capacity: 8 });
    await ctx.plugin({ inject: ['initializers', 'actors'], apply(pluginCtx) {
      pluginCtx.initializers.register({ id: 'seed', after: [], async initialize(context) {
        await context.database.transaction(context.writeLease, req('seed'), async tx => {
          for (const [index, id] of actors.entries()) await pluginCtx.actors.create(tx, { id, attributes: { index, count: 0 } });
        });
        return { operationIds: ['seed'] };
      } });
    } });
    const root = await ctx.sqliteBootstrap.create({
      operationId: 'create', runId, directory, input: {},
      migrations: [artifactsMigration, actorsMigration, ...(options.deferred ? [deferredWorkMigration({ consumer: 'metrics-test' })] : []), ...(options.migrations ?? [])],
      initialFrontier: { codec: 'staged/1', value: '0', logicalTime: '0', completedCount: '0', pendingAcknowledgements: [] },
      initialControl: { generation: 'g', controlSequence: '0', planId: 'metrics-plan' },
      requiredInitializerIds: ['seed']
    });
    await ctx.plugin(ClockTicks);
    await ctx.plugin(ProposalSequence, { proposals: proposals(ticks, actors) });
    await ctx.plugin(RuntimeStaged);
    await ctx.plugin(CompletionStrict);
    await ctx.plugin(CheckpointPolicy, { every: options.checkpointEvery ?? 100000 });
    await ctx.plugin(Termination);
    await ctx.plugin(GenerationViews);
    await ctx.plugin(GenerationBinding, { generation: 'g' });
    await ctx.plugin(AgentRuntimes);
    const Metrics = await metricsPlugin();
    if (Metrics) await ctx.plugin(Metrics);
    if (options.deferred) await ctx.plugin(DeferredWork, options.deferredConfig ?? {});
    await ctx.plugin(RunController, options.runsConfig ?? {});
    ctx.drivers.register('rule', { async activate(intent, context) {
      const operationId = 'work-' + intent.id;
      if (options.onActivate) await options.onActivate(intent, context, ctx);
      await context.database.transaction(context.writeLease, req(operationId), async tx => {
        if (options.work === 'quadratic') {
          await tx.execute({ sql: 'INSERT INTO curve(v) VALUES(?)', parameters: [BigInt(intent.input?.tick ?? 0)] });
          await tx.query({ sql: 'SELECT v FROM curve' }, { maxRows: 10000, maxBytes: 1048576 });
        }
        else if (options.work === 'constant') {
          await tx.execute({ sql: 'INSERT INTO curve(v) VALUES(1)', parameters: [] });
          await tx.query({ sql: 'SELECT v FROM curve WHERE id=?', parameters: [1n] }, { maxRows: 1, maxBytes: 64 });
        }
        const actor = await ctx.actors.get(intent.actorId, tx);
        await ctx.actors.update(tx, { id: actor.id, attributes: { ...actor.attributes, count: Number(actor.attributes.count ?? 0) + 1 } });
      });
      return { status: 'completed', value: null, evidence: {}, requiredOperations: [operationId] };
    } });
    return { host, ctx, directory, root, deferred: ctx.get('deferredWork', false), run: () => ctx.runs.run(new AbortController().signal) };
  }
  catch (error) {
    await host.close();
    throw error;
  }
}
