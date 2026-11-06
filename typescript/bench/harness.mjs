/**
 * C9 基准共用装配：真实核心插件 + metrics 插件 + 可替换的激活工作负载。
 * 与 B0 的 bench/public-vfs.mjs 共用目录与 JSON 报告字段约定。
 */
import { mkdtemp, readFile } from 'node:fs/promises';
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
import Metrics from '../.build/core/dist/metrics/index.js';

export const REQUIRED_REPORT_FIELDS = ['schemaVersion', 'mode', 'generatedAt', 'scale', 'operations', 'counters', 'durationMs'];

export const request = operationId => ({
    operationId, maxDurationMs: 60000, maxStatements: 4000, maxInputBytes: 1048576,
    maxChangedRows: 100000, maxCapturedBytes: 4194304
});

export const workloadMigration = {
    owner: 'c9-bench', from: 0, to: 1, after: [], source: { consumer: 'c9-bench' },
    statements: [{ sql: 'CREATE TABLE bench_work(id INTEGER PRIMARY KEY NOT NULL,v INTEGER NOT NULL);' }]
};

export function proposals(ticks, actorIds, source = 'agenda') {
    return Array.from({ length: ticks }, (_, tick) => ({
        id: 'p' + tick,
        planKind: 'staged/1',
        payload: {
            phases: [{ id: 'act', activations: actorIds.map((actorId, index) => ({
                id: 'a' + tick + '-' + index, actorId, momentId: 'm' + tick, source, driver: 'bench', input: { tick, index }
            })) }],
            nextCursor: String(tick + 1)
        }
    }));
}

function fakeProviderModel({ latencyBaseMs, latencyPerTokenMs, kvPenalty, kvThreshold, promptTokens }) {
    const prompt = promptTokens ?? 512;
    const modeledLatencyMs = latencyBaseMs + latencyPerTokenMs * prompt + (prompt > kvThreshold ? kvPenalty : 0);
    return { promptTokens: prompt, outputTokens: Math.round(prompt / 8), cacheRead: Math.round(prompt * 0.25), cacheWrite: 0, modeledLatencyMs };
}

/**
 * options:
 *  - actors: string[] 或数量
 *  - ticks, work: 'constant' | 'quadratic' | 'fake-llm'
 *  - checkpointEvery
 */
export async function fixture(options = {}) {
    const host = await createHost({ boot: [] });
    const ctx = host.ctx;
    const directory = options.directory ?? await mkdtemp(join(tmpdir(), 'c9-bench-'));
    const runId = options.runId ?? 'c9-bench';
    const actorIds = Array.isArray(options.actors) ? options.actors : Array.from({ length: options.actors ?? 100 }, (_, index) => index === 0 ? 'a' : 'a' + index);
    const ticks = options.ticks ?? 20;
    const model = fakeProviderModel(options.fakeLLM ?? {});
    const counters = { activations: 0, llmRequests: 0, promptTokens: 0, outputTokens: 0, cacheRead: 0, toolCalls: 0, workRows: 0, modeledLatencyMs: 0 };
    try {
        for (const plugin of [OwnedResources, TaskSupervisor, CompositionStrategies, SlotStrategies, Slots, ExecutionFence]) await ctx.plugin(plugin);
        await ctx.plugin(SqliteDatabase, { directory, runId, generation: 'g', maxTransactionMs: 60000 });
        await ctx.plugin(ArtifactsLocal, { directory: join(directory, 'artifacts') });
        await ctx.plugin(Initializers);
        await ctx.plugin(SqliteBootstrap, { maxTransactionMs: 30000 });
        await ctx.plugin(SqliteHistory, { directory, runId, generation: 'g', controlSequence: '0' });
        await ctx.plugin(Actors);
        await ctx.plugin(ActorEligibility);
        await ctx.plugin(Drivers);
        await ctx.plugin(ActivationMailbox);
        await ctx.plugin(Activation, options.activationConfig ?? { capacity: 8, maxActivations: Math.max(2048, actorIds.length * ticks + 16), maxQueuedBytes: 64 * 1024 * 1024 });
        await ctx.plugin({ inject: ['initializers', 'actors'], apply(pluginCtx) {
            pluginCtx.initializers.register({ id: 'seed', after: [], async initialize(context) {
                const chunk = 500, operationIds = [];
                for (let offset = 0; offset < actorIds.length; offset += chunk) {
                    operationIds.push('seed-' + offset);
                    const part = actorIds.slice(offset, offset + chunk);
                    await context.database.transaction(context.writeLease, request('seed-' + offset), async tx => {
                        for (const [index, id] of part.entries()) await pluginCtx.actors.create(tx, { id, attributes: { index: offset + index, count: 0 } });
                    });
                }
                return { operationIds };
            } });
        } });
        const root = await ctx.sqliteBootstrap.create({
            operationId: 'create', runId, directory, input: {},
            migrations: [artifactsMigration, actorsMigration, workloadMigration],
            initialFrontier: { codec: 'staged/1', value: '0', logicalTime: '0', completedCount: '0', pendingAcknowledgements: [] },
            initialControl: { generation: 'g', controlSequence: '0', planId: 'c9-bench-plan' },
            requiredInitializerIds: ['seed']
        });
        await ctx.plugin(ClockTicks);
        await ctx.plugin(ProposalSequence, { proposals: proposals(ticks, actorIds) });
        await ctx.plugin(RuntimeStaged);
        await ctx.plugin(CompletionStrict);
        await ctx.plugin(CheckpointPolicy, { every: options.checkpointEvery ?? 10 });
        await ctx.plugin(Termination);
        await ctx.plugin(GenerationViews);
        await ctx.plugin(GenerationBinding, { generation: 'g' });
        await ctx.plugin(AgentRuntimes);
        await ctx.plugin(Metrics);
        await ctx.plugin(RunController, options.runsConfig ?? {});
        ctx.drivers.register('bench', { async activate(intent, context) {
            const operationId = 'work-' + intent.id;
            await context.database.transaction(context.writeLease, request(operationId), async tx => {
                counters.activations++;
                if (options.work === 'quadratic') {
                    await tx.execute({ sql: 'INSERT INTO bench_work(v) VALUES(?)', parameters: [BigInt(intent.input?.tick ?? 0)] });
                    const rows = await tx.query({ sql: 'SELECT v FROM bench_work' }, { maxRows: 10000, maxBytes: 1048576 });
                    counters.workRows += rows.length;
                }
                else if (options.work === 'fake-llm') {
                    counters.llmRequests++;
                    counters.promptTokens += model.promptTokens;
                    counters.outputTokens += model.outputTokens;
                    counters.cacheRead += model.cacheRead;
                    counters.modeledLatencyMs += model.modeledLatencyMs;
                    const progress = ctx.get('metrics');
                    progress?.observe('llm.latencyMs', model.modeledLatencyMs);
                    progress?.count('llm.requests');
                    progress?.observe('llm.promptTokens', model.promptTokens);
                    progress?.count('llm.outputTokens', model.outputTokens);
                    progress?.count('llm.cacheReadTokens', model.cacheRead);
                    progress?.count('tools.calls');
                    progress?.count('tools.byName.act');
                    counters.toolCalls++;
                }
                else {
                    await tx.execute({ sql: 'INSERT INTO bench_work(v) VALUES(1)', parameters: [] });
                    await tx.query({ sql: 'SELECT v FROM bench_work WHERE id=?', parameters: [1n] }, { maxRows: 1, maxBytes: 64 });
                }
                const actor = await ctx.actors.get(intent.actorId, tx);
                await ctx.actors.update(tx, { id: actor.id, attributes: { ...actor.attributes, count: Number(actor.attributes.count ?? 0) + 1 } });
            });
            return { status: 'completed', value: null, evidence: {}, requiredOperations: [operationId] };
        } });
        return {
            host, ctx, directory, root, counters, model, actorCount: actorIds.length, ticks,
            run: () => ctx.runs.run(new AbortController().signal),
            async metrics() {
                const text = await readFile(join(directory, 'metrics.jsonl'), 'utf8');
                return text.trimEnd().split('\n').map(line => JSON.parse(line));
            }
        };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}
