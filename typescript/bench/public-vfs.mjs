// B0 基准框架：/public 与现状 VFS 的合成数据生成器和驱动（DESIGN §12.3）。
// 只做本地合成数据与真实插件装配，不调用 LLM、不引入资源上限。
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHost, OwnedResources, TaskSupervisor } from '../.build/host/dist/index.js';
import { CompositionStrategies } from '../.build/host/dist/composition-strategies/index.js';
import strategies from '../.build/host/dist/slot-strategies/index.js';
import { Slots } from '../.build/host/dist/slots/index.js';
import { ExecutionFence } from '../.build/core/dist/execution-fence/index.js';
import { SqliteDatabase } from '../.build/core/dist/sqlite/index.js';
import { ArtifactsLocal, artifactsMigration } from '../.build/core/dist/artifacts-local/index.js';
import { Initializers } from '../.build/core/dist/initializers/index.js';
import { SqliteBootstrap } from '../.build/core/dist/sqlite-bootstrap/index.js';
import { SqliteHistory } from '../.build/core/dist/sqlite-history/index.js';
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
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';
import { searchProcesses } from '../.build/core/dist/exploration/search.js';
import { identity, page, fail, after, equal } from '../.build/core/dist/information/page.js';

export const REQUIRED_REPORT_FIELDS = ['schemaVersion', 'mode', 'generatedAt', 'scale', 'operations', 'counters', 'durationMs'];
export const REQUIRED_OPERATIONS = ['ls', 'stat', 'read', 'find', 'grep', 'bashStart', 'concurrent'];

const MIGRATION_SOURCE = { consumer: 'b0-public-vfs-bench' };
const benchMigration = {
    owner: 'bench', from: 0, to: 1, after: [], source: MIGRATION_SOURCE,
    statements: [{ sql: `CREATE TABLE bench_files(path TEXT PRIMARY KEY NOT NULL,dir TEXT NOT NULL,bytes INTEGER NOT NULL,body TEXT NOT NULL);CREATE INDEX bench_files_dir ON bench_files(dir,path);` }]
};
const transactionRequest = operationId => ({
    operationId, maxDurationMs: 120000, maxStatements: 200000, maxInputBytes: 1048576,
    maxChangedRows: 200000, maxCapturedBytes: 16777216
});
const scope = { actorId: 'a', momentId: 'm', query: null };

/** 固定种子：同一 (files, rowsPerFile, bucketSize) 生成同一份数据。 */
export function generateData({ files, rowsPerFile = 20, bucketSize = 100 }) {
    const rows = [];
    let bytes = 0;
    for (let file = 0; file < files; file++) {
        const bucket = 'b' + String(Math.floor(file / bucketSize)).padStart(4, '0');
        const path = `/world/bench/files/${bucket}/f${String(file).padStart(6, '0')}.jsonl`;
        const lines = [];
        for (let row = 0; row < rowsPerFile; row++) {
            const note = row === 0 && file % 97 === 0 ? 'needle-' + (file % 13) : 'value-' + (file % 101);
            lines.push(JSON.stringify({ id: `f${file}-r${row}`, kind: 'price', value: (file * 31 + row * 7) % 1000, note }));
        }
        const body = lines.join('\n') + '\n';
        bytes += Buffer.byteLength(body);
        rows.push({ path, dir: `/world/bench/files/${bucket}`, bytes: Buffer.byteLength(body), body });
    }
    return { rows, bytes, buckets: Math.ceil(files / bucketSize) };
}

function benchSource() {
    return {
        id: 'bench.files', schema: { type: 'object', properties: { op: { type: 'string' } } }, capabilities: [],
        available: async () => true,
        async query(input, _scope, view, request) {
            const bounds = { maxRows: Math.max(1, request.maxItems + 1), maxBytes: 1048576 };
            if (input.op === 'stat' || input.op === 'read') {
                const rows = await view.query({ sql: 'SELECT path,dir,bytes,body FROM bench_files WHERE path=?', parameters: [String(input.path)] }, bounds);
                const items = rows.map(row => ({ kind: 'inline', value: { path: String(row.path), dir: String(row.dir), bytes: Number(row.bytes), body: String(row.body) } }));
                return { items, total: String(items.length), revision: view.revision, responseBytes: 0 };
            }
            if (input.op === 'list') {
                const dir = String(input.dir), last = String(input.after ?? '');
                const [count] = await view.query({ sql: 'SELECT count(*) AS n FROM bench_files WHERE dir=?', parameters: [dir] }, { maxRows: 1, maxBytes: 65536 });
                const rows = await view.query({ sql: 'SELECT path,bytes FROM bench_files WHERE dir=? AND path>? ORDER BY path LIMIT ?', parameters: [dir, last, request.maxItems + 1] }, { maxRows: request.maxItems + 1, maxBytes: bounds.maxBytes });
                const items = rows.slice(0, request.maxItems).map(row => ({ kind: 'inline', value: { path: String(row.path), bytes: Number(row.bytes) } }));
                return { items, total: String(count.n), revision: view.revision, responseBytes: 0 };
            }
            return fail('BENCH_QUERY');
        }
    };
}

function benchMount(ctx, state) {
    const prefix = '/world/bench', filesRoot = prefix + '/files', scopeRoot = prefix + '/scope';
    const bucketPath = bucket => filesRoot + '/' + bucket;
    const dirNode = (path, scope_, view) => ({ path, objectId: JSON.stringify([view.runId, scope_.actorId, path]), kind: 'directory', versions: [{ owner: 'bench:' + state.id, version: view.revision }] });
    const fileNode = (row, scope_, view) => ({ path: String(row.path), objectId: JSON.stringify([view.runId, scope_.actorId, String(row.path)]), kind: 'file', versions: [{ owner: 'bench:' + state.id, version: view.revision }], rawBytes: String(row.bytes) });
    // 数据由 information 来源持有；VFS 挂载直接在该来源的 SQL 上读取，测的是 VFS 读路径本身。
    const info = (input, scope_, view, request) => state.source.query(input, scope_, view, request);
    const statFile = async (path, scope_, view) => {
        const result = await info({ op: 'stat', path }, scope_, view, { maxItems: 1, maxResponseBytes: 65536 });
        if (!result.items.length) return fail('BENCH_NOT_FOUND');
        return fileNode(result.items[0].value, scope_, view);
    };
    return {
        id: 'bench/world', prefix,
        async visible(path) {
            return path === prefix || path.startsWith(prefix + '/');
        },
        async stat(path, scope_, view) {
            if (path === prefix || path === filesRoot || path === scopeRoot || new RegExp('^' + filesRoot + '/b[0-9]+$').test(path)) return dirNode(path, scope_, view);
            return statFile(path, scope_, view);
        },
        async list(path, scope_, view, request) {
            const id = identity(scope_, view, state.id + ':list');
            if (path === prefix) return page([dirNode(filesRoot, scope_, view)], '1', view.revision, request, id, node => node.path);
            if (path === scopeRoot) {
                const nodes = Array.from({ length: state.scopeBuckets }, (_, index) => dirNode(bucketPath('b' + String(index).padStart(4, '0')), scope_, view));
                return page(nodes, String(nodes.length), view.revision, request, id, node => node.path);
            }
            if (path === filesRoot) {
                const nodes = Array.from({ length: state.buckets }, (_, index) => dirNode(bucketPath('b' + String(index).padStart(4, '0')), scope_, view));
                return page(nodes, String(nodes.length), view.revision, request, id, node => node.path);
            }
            const last = after(request.cursor, id) ?? '';
            // 多要一项，page() 才能判断还有下一页（否则最后一页会静默截断）。
            const result = await info({ op: 'list', dir: path, after: last }, scope_, view, { ...request, maxItems: request.maxItems + 1 });
            const nodes = result.items.map(item => fileNode(item.value, scope_, view));
            return page(nodes, result.total, view.revision, request, id, node => node.path);
        },
        async open(node, scope_, view) {
            const current = await statFile(node.path, scope_, view);
            if (!equal(current, node)) return fail('BENCH_STALE');
            const result = await info({ op: 'read', path: node.path }, scope_, view, { maxItems: 1, maxResponseBytes: 65536 });
            const raw = Buffer.from(String(result.items[0].value.body));
            let released = false;
            return {
                value: { node, read: async function* (range, signal) {
                    signal.throwIfAborted();
                    if (released) return fail('BENCH_RELEASED');
                    const start = Number(range.offset);
                    if (!Number.isSafeInteger(start) || start < 0 || range.length > range.maxBytes || start + range.length > raw.length) return fail('PAYLOAD_RANGE');
                    yield raw.subarray(start, start + range.length);
                } }, release: async () => { released = true; }
            };
        },
        async collect(path, query, scope_, view, request) { return info({ op: 'list', dir: path, after: '' }, scope_, view, request); }
    };
}

async function assemble(state) {
    const host = await createHost({ boot: [] });
    try {
        const base = state.directory, run = join(base, 'run');
        await host.ctx.plugin(OwnedResources);
        await host.ctx.plugin(TaskSupervisor, { concurrency: 8, maxBytes: 33554432 });
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(ExecutionFence);
        await host.ctx.plugin(SqliteDatabase, { directory: run, runId: 'b0-bench', generation: 'g', maxTransactionMs: 600000 });
        await host.ctx.plugin(ArtifactsLocal, { directory: join(run, 'objects') });
        await host.ctx.plugin(Initializers);
        await host.ctx.plugin(SqliteBootstrap);
        await host.ctx.plugin(SqliteHistory, { directory: run, runId: 'b0-bench', generation: 'g', controlSequence: '0' });
        await host.ctx.plugin(Attempts, { directory: join(base, 'attempts'), lineage: 'b0-bench' });
        await host.ctx.attempts.initialize();
        const source = MIGRATION_SOURCE;
        await host.ctx.database.applyMigrations([artifactsMigration, actorsMigration, actionsMigration(source), threadMigration(source), resultsMigration(source), perceptionMigration(source), workspaceMigration(source), benchMigration]);
        await host.ctx.plugin(Actors);
        await host.ctx.plugin(ActorEligibility);
        await host.ctx.plugin(Actions);
        await host.ctx.plugin(Information);
        await host.ctx.plugin(Threads);
        await host.ctx.plugin(Results);
        await host.ctx.plugin(Perceptions);
        await host.ctx.plugin(Workspace);
        await host.ctx.plugin(Exploration, {});
        await host.ctx.plugin(mountWorkspace);
        await host.ctx.plugin(mountResults);
        await host.ctx.plugin(mountWorld);
        await host.ctx.plugin(ExplorationShell);
        let ctx;
        await host.ctx.plugin({ inject: ['database', 'executionFence', 'artifacts', 'attempts', 'actors', 'actorEligibility', 'actions', 'information', 'threads', 'results', 'perceptions', 'workspace', 'exploration', 'explorationShell'], apply(c) { ctx = c; } });
        return { host, ctx, run, base };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}

async function seed(ctx, reservation, state, options) {
    const request = operationId => transactionRequest(operationId);
    await ctx.database.transaction(reservation.lease, request('b0-seed-actors'), async tx => {
        for (let index = 0; index < options.actors; index++) {
            const id = index === 0 ? 'a' : 'a' + index;
            await ctx.actors.create(tx, { id, attributes: { index } });
            if (index < 64) await ctx.workspace.initialize(tx, id);
        }
    });
    const chunk = 500;
    for (let offset = 0; offset < state.rows.length; offset += chunk) {
        const part = state.rows.slice(offset, offset + chunk);
        await ctx.database.transaction(reservation.lease, request('b0-seed-files-' + offset), async tx => {
            await tx.executeMany('INSERT INTO bench_files VALUES(?,?,?,?)', part.map(row => [row.path, row.dir, row.bytes, row.body]), { maxRows: chunk, maxInputBytes: 1048576 });
        });
    }
}

function percentile(values, p) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p / 100 * sorted.length) - 1))];
}
function summary(values, errors) {
    return { samples: values.length, p50Ms: percentile(values, 50), p95Ms: percentile(values, 95), p99Ms: percentile(values, 99), errors };
}
async function measure(samples, body) {
    const values = [];
    let errors = 0;
    const failures = [];
    for (let index = 0; index < samples; index++) {
        const started = performance.now();
        try { await body(index); }
        catch (error) {
            errors++;
            if (failures.length < 3) failures.push(String(error?.message ?? error));
        }
        values.push(performance.now() - started);
    }
    return { ...summary(values, errors), ...(failures.length ? { failures } : {}) };
}

export async function runBench(options = {}) {
    const startedAt = performance.now();
    const state = {
        id: 'b0', directories: [], rows: [], buckets: 0,
        options: {
            actors: options.actors ?? 1000, files: options.files ?? 10000, rowsPerFile: options.rowsPerFile ?? 20,
            bucketSize: options.bucketSize ?? 100, concurrency: options.concurrency ?? 200,
            scopeFiles: options.scopeFiles ?? 2000,
            samples: { ls: 20, stat: 100, read: 20, find: 3, grep: 1, bashStart: 10, ...(options.samples ?? {}) }
        }
    };
    if (options.mode !== undefined && options.mode !== 'current') throw Error('BENCH_MODE ' + options.mode);
    const generated = generateData({ files: state.options.files, rowsPerFile: state.options.rowsPerFile, bucketSize: state.options.bucketSize });
    state.rows = generated.rows;
    state.buckets = generated.buckets;
    state.scopeBuckets = Math.max(1, Math.min(generated.buckets, Math.ceil(Math.min(state.options.scopeFiles, state.options.files) / state.options.bucketSize)));
    state.totalBytes = generated.bytes;
    state.directory = options.directory ?? await mkdtemp(join(tmpdir(), 's0-b0-bench-'));
    const counters = { snapshotOpens: 0, snapshotBudgetErrors: 0 };
    const env = await assemble(state);
    const { ctx } = env;
    const originalSnapshot = ctx.database.snapshot.bind(ctx.database);
    ctx.database.snapshot = async bounds => {
        counters.snapshotOpens++;
        try { return await originalSnapshot(bounds); }
        catch (error) { if (String(error).includes('SQLITE_SNAPSHOT_BUDGET')) counters.snapshotBudgetErrors++; throw error; }
    };
    let reservation, session;
    try {
        reservation = await ctx.executionFence.reserve({ id: 'b0-bench' });
        await seed(ctx, reservation, state, state.options);
        const source = benchSource();
        state.source = source;
        ctx.information.register(source);
        ctx.exploration.register(benchMount(ctx, state));
        const context = operationId => ({
            ctx, database: ctx.database, writeLease: reservation.lease, actorId: 'a', momentId: 'm', logicalTime: 1,
            runId: 'b0-bench', generation: 'g', operationId, signal: reservation.signal,
            transaction: (request, body) => ctx.database.transaction(reservation.lease, request, body)
        });
        const invocation = context('b0-bench');
        session = await ctx.exploration.acquire(scope, invocation);
        const filesRoot = '/world/bench/files', scopeRoot = '/world/bench/scope';
        const filePath = index => state.rows[Math.floor(index) % state.rows.length].path;
        const bucket = 'b' + String(Math.floor((state.rows.length / 2) / state.options.bucketSize)).padStart(4, '0');
        const pageRequest = (maxItems, maxResponseBytes) => ({ maxItems, maxResponseBytes });
        const operations = {};
        const resetSnapshot = () => { counters.snapshotOpens = 0; counters.snapshotBudgetErrors = 0; };

        resetSnapshot();
        operations.ls = { ...await measure(state.options.samples.ls, async () => { const listed = await session.value.list(filesRoot + '/' + bucket, pageRequest(64, 65536)); if (listed.items.length < 1) throw Error('BENCH_LS_EMPTY'); }), extra: { items: Math.min(64, state.options.bucketSize) } };
        operations.ls.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.stat = { ...await measure(state.options.samples.stat, index => session.value.stat(filePath(index))), extra: {} };
        operations.stat.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.read = { ...await measure(state.options.samples.read, async index => {
            const node = await session.value.stat(filePath(index));
            const handle = await session.value.open(node);
            try {
                let read = 0;
                for await (const chunk of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)) read += chunk.length;
                if (read <= 0) throw Error('BENCH_READ_EMPTY');
            }
            finally { await handle.release(); }
        }), extra: {} };
        operations.read.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.find = { ...await measure(state.options.samples.find, () => session.value.find({ root: scopeRoot, glob: '**/*.jsonl', page: pageRequest(state.scopeBuckets * state.options.bucketSize + 16, 16777216) })), extra: {} };
        operations.find.extra.nodes = state.scopeBuckets * state.options.bucketSize;
        operations.find.extra.scopeFiles = state.scopeBuckets * state.options.bucketSize;
        operations.find.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        const rgBefore = searchProcesses.started;
        operations.grep = { ...await measure(state.options.samples.grep, () => session.value.grep({ root: scopeRoot, pattern: 'needle', mode: 'literal', page: pageRequest(state.scopeBuckets * state.options.bucketSize + 16, 16777216) })), extra: {} };
        operations.grep.extra.rgProcesses = searchProcesses.started - rgBefore;
        operations.grep.extra.scopeFiles = state.scopeBuckets * state.options.bucketSize;
        operations.grep.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.bashStart = { ...await measure(state.options.samples.bashStart, () => ctx.explorationShell.execute('true', session.value, invocation)), extra: {} };
        operations.bashStart.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        const concurrentStarted = performance.now();
        const results = await Promise.allSettled(Array.from({ length: state.options.concurrency }, (_, index) => session.value.stat(filePath(index))));
        operations.concurrent = {
            samples: state.options.concurrency,
            p50Ms: null, p95Ms: null, p99Ms: null,
            errors: results.filter(result => result.status === 'rejected').length,
            extra: { wallMs: performance.now() - concurrentStarted, sqliteSnapshotBudgetErrors: counters.snapshotBudgetErrors }
        };
        operations.concurrent.snapshotOpens = counters.snapshotOpens;

        return {
            schemaVersion: 1,
            mode: 'current',
            generatedAt: new Date().toISOString(),
            scale: {
                actors: state.options.actors, files: state.options.files, rowsPerFile: state.options.rowsPerFile,
                buckets: state.buckets, bucketSize: state.options.bucketSize, totalRows: state.options.files * state.options.rowsPerFile,
                findScopeFiles: state.scopeBuckets * state.options.bucketSize, grepScopeFiles: state.scopeBuckets * state.options.bucketSize,
                totalBytes: state.totalBytes, concurrency: state.options.concurrency
            },
            operations,
            counters: {
                renderCount: null, cacheHitRate: null,
                rgProcesses: operations.grep.extra.rgProcesses,
                snapshotOpens: Object.fromEntries(Object.entries(operations).map(([name, operation]) => [name, operation.snapshotOpens])),
                readerQueueWaitMs: null,
                sqliteSnapshotBudgetErrors: counters.snapshotBudgetErrors
            },
            durationMs: performance.now() - startedAt
        };
    }
    finally {
        try { await session?.release(); } catch {}
        try { await reservation?.release(); } catch {}
        await env.host.close();
        await rm(state.directory, { recursive: true, force: true });
    }
}

function argument(argv, name, fallback) {
    const index = argv.indexOf('--' + name);
    if (index < 0) return fallback;
    const value = argv[index + 1];
    if (value === undefined) throw Error('缺少 --' + name + ' 的值');
    return value;
}
function numberArgument(argv, name, fallback) {
    const raw = argument(argv, name, undefined);
    if (raw === undefined) return fallback;
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 1) throw Error('--' + name + ' 必须是正整数');
    return value;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    const argv = process.argv.slice(2);
    const samples = numberArgument(argv, 'samples', undefined);
    const report = await runBench({
        actors: numberArgument(argv, 'actors', 1000),
        files: numberArgument(argv, 'files', 10000),
        rowsPerFile: numberArgument(argv, 'rows', 20),
        concurrency: numberArgument(argv, 'concurrency', 200),
        scopeFiles: numberArgument(argv, 'scope-files', 2000),
        samples: samples === undefined ? undefined : { ls: samples, stat: samples, read: samples, find: Math.max(1, Math.floor(samples / 4)), grep: 1, bashStart: samples }
    });
    const out = argument(argv, 'out', undefined);
    if (out) {
        await mkdir(dirname(out), { recursive: true });
        await writeFile(out, JSON.stringify(report, null, 2) + '\n');
    }
    console.log(JSON.stringify(report));
}
