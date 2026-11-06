/**
 * B3 history 基准（DESIGN §6.2）：空步链的发布、物化与分叉。
 * 只做本地合成数据，不调用 LLM。链长度可配置，默认 200/1000/10001。
 */
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
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
import Attempts from '../.build/core/dist/attempts/index.js';

export const REQUIRED_REPORT_FIELDS = ['schemaVersion', 'mode', 'generatedAt', 'scale', 'operations', 'counters', 'durationMs'];
const request = operationId => ({ operationId, maxDurationMs: 60000, maxStatements: 1000, maxInputBytes: 1048576, maxChangedRows: 10000, maxCapturedBytes: 4194304 });
const frontier = n => ({ codec: 'step/1', value: String(n), logicalTime: String(n), completedCount: String(n), pendingAcknowledgements: [] });

export async function runChain(length, options = {}) {
    const base = options.directory ?? await mkdtemp(join(tmpdir(), 'c9-history-'));
    await mkdir(base, { recursive: true });
    const directory = join(base, 'world');
    const runId = 'c9-history-' + length;
    const host = await createHost({ boot: [] });
    const ctx = host.ctx;
    const timings = { publishMs: [], transactionMs: [] };
    let cut;
    try {
        for (const plugin of [OwnedResources, TaskSupervisor, CompositionStrategies, SlotStrategies, Slots, ExecutionFence]) await ctx.plugin(plugin);
        await ctx.plugin(SqliteDatabase, { directory, runId, generation: 'g', maxTransactionMs: 60000 });
        await ctx.plugin(ArtifactsLocal, { directory: join(directory, 'artifacts') });
        await ctx.plugin(Initializers);
        await ctx.plugin(SqliteBootstrap, { maxTransactionMs: 60000 });
        await ctx.plugin(SqliteHistory, { directory, runId, generation: 'g', controlSequence: '0', ...(options.historyConfig ?? {}) });
        await ctx.plugin(Attempts, { directory: join(base, 'lineage'), lineage: 'c9-history' });
        await ctx.plugin({ inject: ['initializers', 'attempts'], apply(pluginCtx) {
            pluginCtx.initializers.register({ id: 'seed', after: [], async initialize(context) {
                await pluginCtx.attempts.initialize();
                await context.database.transaction(context.writeLease, request('seed'), async tx => {
                    await tx.execute({ sql: 'INSERT INTO note VALUES(1,?)', parameters: ['root'] });
                });
                return { operationIds: ['seed'] };
            } });
        } });
        cut = await ctx.sqliteBootstrap.create({
            operationId: 'create', runId, directory, input: {},
            migrations: [artifactsMigration, { owner: 'note', from: 0, to: 1, after: ['artifacts-local'], source: { consumer: 'c9-history' }, statements: [{ sql: 'CREATE TABLE note(id INTEGER PRIMARY KEY NOT NULL,v TEXT NOT NULL);' }] }],
            initialFrontier: frontier(0), initialControl: { generation: 'g', controlSequence: '0', planId: 'c9-history' }, requiredInitializerIds: ['seed']
        });
        await ctx.plugin({ inject: ['database', 'history', 'executionFence'], apply(c) { ctx.history.bindQuiescence(async () => {}); } });
        const started = performance.now();
        for (let step = 1; step <= length; step++) {
            const operationId = 'step-' + step;
            const transactionStarted = performance.now();
            const reservation = await ctx.executionFence.reserve({ id: operationId });
            try {
                await ctx.database.transaction(reservation.lease, request(operationId), async tx => {
                    await tx.execute({ sql: 'UPDATE note SET v=? WHERE id=1', parameters: ['v' + step] });
                    await ctx.database.metadata.complete(tx, frontier(step), []);
                });
            }
            finally { await reservation.release(); }
            timings.transactionMs.push(performance.now() - transactionStarted);
            const publishStarted = performance.now();
            const lease = await ctx.executionFence.seal();
            cut = await ctx.history.publish(lease, { operationId: 'publish-' + step, id: 'point-' + step, parentId: cut.id, expectedRevision: await ctx.database.readRevision(), requiredOperations: [operationId] });
            ctx.executionFence.resume(lease);
            timings.publishMs.push(performance.now() - publishStarted);
            if (step % 100 === 0 && options.onProgress) options.onProgress(step, performance.now() - started);
        }
        const publishTotalMs = performance.now() - started;
        // 10001 点链只验证发布与不触发预算错误；物化与分叉在 heavyLimit 内才跑。
        const heavy = length <= (options.heavyLimit ?? 1000);
        let compactMs = null, forkMs = null, fork;
        if (heavy) {
            const compactStarted = performance.now();
            await ctx.history.compact(cut.id);
            compactMs = performance.now() - compactStarted;
            const forkDirectory = join(base, 'fork');
            const forkStarted = performance.now();
            try {
                fork = await ctx.history.fork({ operationId: 'fork-' + length, pointId: cut.id, targetRunId: runId + '-fork', targetDirectory: forkDirectory, migrations: [artifactsMigration] });
            }
            catch (error) { fork = { error: String(error) }; }
            forkMs = performance.now() - forkStarted;
        }
        const stats = ctx.history.diagnostics();
        return {
            length,
            status: fork?.error ? 'partial' : 'measured',
            publishTotalMs,
            publishP50Ms: percentile(timings.publishMs, 50),
            publishP95Ms: percentile(timings.publishMs, 95),
            transactionP95Ms: percentile(timings.transactionMs, 95),
            compactMs,
            forkMs,
            fork: fork?.error ? { error: fork.error } : { id: fork.id, segments: fork.segments.length },
            history: { segmentsSealed: stats.segmentsSealed, applyCount: stats.applyCount, maxRSS: stats.maxRSS, timings: stats.timings }
        };
    }
    finally {
        await host.close();
        if (!options.keep) await rm(base, { recursive: true, force: true });
    }
}

function percentile(values, p) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p / 100 * sorted.length) - 1))];
}

export async function runBench(options = {}) {
    const started = performance.now();
    const lengths = options.lengths ?? [200, 1000, 10001];
    const chains = [];
    for (const length of lengths) {
        try { chains.push(await runChain(length, { onProgress: options.onProgress, directory: options.directory ? join(options.directory, 'chain-' + length) : undefined })); }
        catch (error) { chains.push({ length, status: 'failed', error: String(error?.message ?? error) }); }
    }
    return {
        schemaVersion: 1,
        mode: 'history',
        generatedAt: new Date().toISOString(),
        scale: { lengths },
        operations: Object.fromEntries(Object.entries({ 200: 'chain', 1000: 'chain', 10001: 'chain' })),
        chains,
        counters: { chains: chains.length, failed: chains.filter(item => item.status !== 'measured').length },
        durationMs: performance.now() - started
    };
}

async function main() {
    const argv = process.argv.slice(2);
    const value = name => { const index = argv.indexOf('--' + name); return index < 0 ? undefined : argv[index + 1]; };
    const lengths = value('lengths') ? value('lengths').split(',').map(Number) : undefined;
    const report = await runBench({ lengths, directory: value('directory') });
    if (value('out')) {
        const { mkdir, writeFile } = await import('node:fs/promises');
        await mkdir(dirname(resolve(value('out'))), { recursive: true });
        await writeFile(resolve(value('out')), JSON.stringify(report, null, 2) + '\n');
    }
    console.log(JSON.stringify(report));
    if (report.counters.failed) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
