// B0/B10 基准框架：/public、/self 与现状 VFS 的合成数据生成器和驱动（DESIGN §12.3）。
// 只做本地合成数据与真实插件装配，不调用 LLM、不引入资源上限。
import { mkdtemp, mkdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir, loadavg } from 'node:os';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Session } from 'node:inspector';
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
import Public, { publicMigration } from '../.build/core/dist/public/index.js';
import MomentViews, { momentViewsMigration } from '../.build/core/dist/moment-views/index.js';
import PublicSelf from '../.build/core/dist/public-self/index.js';
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
const publicTransactionRequest = operationId => ({
    operationId, maxDurationMs: 120000, maxStatements: 400000, maxInputBytes: 16777216,
    maxChangedRows: 400000, maxCapturedBytes: 67108864
});
const scope = { actorId: 'a', momentId: 'm', query: null };

// /public 基准布局：/public/bench/files/<bucket>/<file>.jsonl，与现状模式的桶一一对应。
const PUBLIC_FILES = '/public/bench/files';
const PUBLIC_LARGE = '/public/bench/large';
const PUBLIC_WRITES = '/public/bench/writes';
const PUBLIC_SCHEMA = {
    type: 'object',
    properties: {
        file: { type: 'string' }, id: { type: 'string' }, kind: { type: 'string' },
        value: { type: 'integer' }, note: { type: 'string' }
    }
};
const SNAPSHOT_LEASE_MS = 30000;
const SNAPSHOT_LEASE_SOURCE = [
    'core/src/exploration/session.ts withView/read 固定 database.snapshot({maxAgeMs:30000})，未读配置',
    'sqlite 默认 maxSnapshotAgeMs=30000（packages/core/src/sqlite/index.ts:3 / database.ts:37）',
    'sqlite/worker.ts:77 与 sqlite/reader.ts:13 在 beginSnapshot 后 setTimeout(maxAgeMs) 关闭快照'
];

function bucketName(index) { return 'b' + String(index).padStart(4, '0'); }
function tablePath(bucket) { return PUBLIC_FILES + '/' + bucket; }
function tableDecl(bucket) {
    return {
        path: tablePath(bucket), title: '基准文件 ' + bucket, description: 'B10 合成数据',
        schema: PUBLIC_SCHEMA, key: ['id'], sort: [{ field: 'file' }, { field: 'id' }],
        shard: { by: 'file', format: 'value' }
    };
}
function largeDecl() {
    return { ...tableDecl('large'), path: PUBLIC_LARGE };
}
function writesDecl() { return { ...tableDecl('writes'), path: PUBLIC_WRITES }; }

/** 固定种子：同一 (files, rowsPerFile, bucketSize) 生成同一份数据。 */
export function generateData({ files, rowsPerFile = 20, bucketSize = 100 }) {
    const rows = [], logical = [];
    let bytes = 0;
    for (let file = 0; file < files; file++) {
        const bucket = bucketName(Math.floor(file / bucketSize));
        const name = 'f' + String(file).padStart(6, '0');
        const path = `/world/bench/files/${bucket}/${name}.jsonl`;
        const lines = [], logicalRows = [];
        for (let row = 0; row < rowsPerFile; row++) {
            const note = row === 0 && file % 97 === 0 ? 'needle-' + (file % 13) : 'value-' + (file % 101);
            lines.push(JSON.stringify({ id: `f${file}-r${row}`, kind: 'price', value: (file * 31 + row * 7) % 1000, note }));
            logicalRows.push({ file: name, id: `f${file}-r${row}`, kind: 'price', value: (file * 31 + row * 7) % 1000, note });
        }
        const body = lines.join('\n') + '\n';
        bytes += Buffer.byteLength(body);
        rows.push({ path, dir: `/world/bench/files/${bucket}`, bytes: Buffer.byteLength(body), body });
        logical.push({ path: `/public/bench/files/${bucket}/${name}.jsonl`, bucket, name, body, rows: logicalRows });
    }
    return { rows, files: logical, bytes, buckets: Math.ceil(files / bucketSize) };
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

async function assembleCurrent(state) {
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

async function assemblePublic(state) {
    const host = await createHost({ boot: [] });
    try {
        const base = state.directory, run = join(base, 'run');
        await host.ctx.plugin(OwnedResources);
        await host.ctx.plugin(TaskSupervisor, { concurrency: 8, maxBytes: 1073741824 });
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(ExecutionFence);
        await host.ctx.plugin(SqliteDatabase, {
            directory: run, runId: 'b10-bench', generation: 'g', maxTransactionMs: 600000,
            maxSnapshots: state.options.maxSnapshots, readerWorkers: state.options.readerWorkers,
            maxSnapshotAgeMs: SNAPSHOT_LEASE_MS, maxQueryBytes: 16777216, maxQueryRows: 200000,
            maxInputBytes: 16777216, maxQueuedBytes: 134217728, busyTimeoutMs: 120000
        });
        await host.ctx.plugin(ArtifactsLocal, { directory: join(run, 'objects') });
        await host.ctx.plugin(Initializers);
        await host.ctx.plugin(SqliteBootstrap);
        await host.ctx.plugin(SqliteHistory, { directory: run, runId: 'b10-bench', generation: 'g', controlSequence: '0' });
        await host.ctx.plugin(Attempts, { directory: join(base, 'attempts'), lineage: 'b10-bench' });
        await host.ctx.attempts.initialize();
        const source = MIGRATION_SOURCE;
        await host.ctx.database.applyMigrations([artifactsMigration, publicMigration(source), momentViewsMigration(source), actorsMigration, actionsMigration(source), threadMigration(source), resultsMigration(source), perceptionMigration(source), workspaceMigration(source)]);
        await host.ctx.plugin(Actors);
        await host.ctx.plugin(ActorEligibility);
        await host.ctx.plugin(Actions);
        await host.ctx.plugin(Information);
        await host.ctx.plugin(Threads);
        await host.ctx.plugin(Results);
        await host.ctx.plugin(Perceptions);
        await host.ctx.plugin(Workspace);
        await host.ctx.plugin(Exploration, { maxInlineFileBytes: 16777216, maxSearchBytes: 134217728, maxSearchOutputBytes: 16777216, maxSearchMs: 120000 });
        await host.ctx.plugin(Public, { cacheDir: join(run, 'public-cache') });
        await host.ctx.plugin(MomentViews, { freeze: 'clock' });
        await host.ctx.plugin(PublicSelf, {});
        await host.ctx.plugin(mountWorkspace);
        await host.ctx.plugin(mountResults);
        await host.ctx.plugin(ExplorationShell, { timeoutMs: state.options.shellTimeoutMs });
        let ctx;
        await host.ctx.plugin({ inject: ['database', 'executionFence', 'artifacts', 'attempts', 'actors', 'actorEligibility', 'actions', 'information', 'threads', 'results', 'perceptions', 'workspace', 'exploration', 'public', 'momentViews', 'publicSelf', 'explorationShell'], apply(c) { ctx = c; } });
        return { host, ctx, run, base };
    }
    catch (error) {
        await host.close();
        throw error;
    }
}

async function seedActors(ctx, reservation, options) {
    await ctx.database.transaction(reservation.lease, transactionRequest('b0-seed-actors'), async tx => {
        for (let index = 0; index < options.actors; index++) {
            const id = index === 0 ? 'a' : 'a' + index;
            await ctx.actors.create(tx, { id, attributes: { index } });
            if (index < 64) await ctx.workspace.initialize(tx, id);
        }
    });
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
function instrument(database) {
    const counters = { snapshotOpens: 0, snapshotBudgetErrors: 0, readerQueueWaitMs: 0 };
    const originalSnapshot = database.snapshot.bind(database);
    database.snapshot = async bounds => {
        counters.snapshotOpens++;
        try { return await originalSnapshot(bounds); }
        catch (error) { if (String(error).includes('SQLITE_SNAPSHOT_BUDGET')) counters.snapshotBudgetErrors++; throw error; }
    };
    // waitSnapshot 是 TS private（运行期仍是普通方法）；只累计等待时间，不改语义。
    if (typeof database.waitSnapshot === 'function') {
        const originalWait = database.waitSnapshot.bind(database);
        database.waitSnapshot = async (...args) => {
            const started = performance.now();
            try { return await originalWait(...args); }
            finally { counters.readerQueueWaitMs += performance.now() - started; }
        };
    }
    return counters;
}
function startRssSampler() {
    const sampler = { peak: process.memoryUsage().rss, samples: 0 };
    const timer = setInterval(() => {
        sampler.samples++;
        const rss = process.memoryUsage().rss;
        if (rss > sampler.peak) sampler.peak = rss;
    }, 200);
    timer.unref();
    return { sampler, stop: () => { clearInterval(timer); const rss = process.memoryUsage().rss; if (rss > sampler.peak) sampler.peak = rss; return sampler.peak; } };
}
async function fileBytes(path) { try { return (await stat(path)).size; } catch { return 0; } }
async function databaseBytes(run) {
    const main = await fileBytes(join(run, 'world.sqlite'));
    const wal = await fileBytes(join(run, 'world.sqlite-wal'));
    const shm = await fileBytes(join(run, 'world.sqlite-shm'));
    return { mainBytes: main, walBytes: wal, shmBytes: shm, totalBytes: main + wal + shm };
}
function cacheHitRate(cache) {
    const hits = cache.memoryHits + cache.diskHits, total = hits + cache.misses;
    return total ? hits / total : null;
}
/** 聚焦单项操作时用程序化 CPU profile，避开装配/播种噪声。 */
function profiler() {
    const session = new Session();
    session.connect();
    const call = (method) => new Promise((resolve, reject) => session.post(method, (error, result) => error ? reject(error) : resolve(result)));
    let active = false;
    return {
        async start() { await call('Profiler.enable'); await call('Profiler.start'); active = true; },
        async stop(path) {
            if (!active) return undefined;
            const { profile } = await call('Profiler.stop');
            active = false;
            session.disconnect();
            await mkdir(dirname(path), { recursive: true });
            await writeFile(path, JSON.stringify(profile) + '\n');
            return path;
        }
    };
}

export async function runBench(options = {}) {
    const mode = options.mode ?? 'current';
    if (mode === 'current') return runCurrentBench(options);
    if (mode === 'public') return runPublicBench(options);
    throw Error('BENCH_MODE ' + mode);
}

async function runCurrentBench(options = {}) {
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
    const generated = generateData({ files: state.options.files, rowsPerFile: state.options.rowsPerFile, bucketSize: state.options.bucketSize });
    state.rows = generated.rows;
    state.buckets = generated.buckets;
    state.scopeBuckets = Math.max(1, Math.min(generated.buckets, Math.ceil(Math.min(state.options.scopeFiles, state.options.files) / state.options.bucketSize)));
    state.totalBytes = generated.bytes;
    state.directory = options.directory ?? await mkdtemp(join(tmpdir(), 's0-b0-bench-'));
    const counters = { snapshotOpens: 0, snapshotBudgetErrors: 0 };
    const env = await assembleCurrent(state);
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
        await seedActors(ctx, reservation, state.options);
        const chunk = 500;
        for (let offset = 0; offset < state.rows.length; offset += chunk) {
            const part = state.rows.slice(offset, offset + chunk);
            await ctx.database.transaction(reservation.lease, transactionRequest('b0-seed-files-' + offset), async tx => {
                await tx.executeMany('INSERT INTO bench_files VALUES(?,?,?,?)', part.map(row => [row.path, row.dir, row.bytes, row.body]), { maxRows: chunk, maxInputBytes: 1048576 });
            });
        }
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

async function runPublicBench(options = {}) {
    const startedAt = performance.now();
    const memory = startRssSampler();
    const state = {
        id: 'b10', rows: [], files: [], buckets: 0,
        options: {
            actors: options.actors ?? 1000, files: options.files ?? 10000, rowsPerFile: options.rowsPerFile ?? 20,
            bucketSize: options.bucketSize ?? 100, concurrency: options.concurrency ?? 200,
            moments: options.moments ?? 100, changeRatio: options.changeRatio ?? 0.01,
            changePercentPerMoment: options.changePercentPerMoment ?? 1,
            checkpoints: options.checkpoints ?? [1, 10, 100],
            shareAgents: options.shareAgents ?? options.actors ?? 1000,
            readerWorkers: options.readerWorkers ?? 4, maxSnapshots: options.maxSnapshots ?? 16,
            shellTimeoutMs: options.shellTimeoutMs ?? 30000,
            samples: { ls: 20, stat: 100, read: 20, find: 5, grep: 5, bashStart: 10, selfRead: 10, ...(options.samples ?? {}) }
        }
    };
    const generated = generateData({ files: state.options.files, rowsPerFile: state.options.rowsPerFile, bucketSize: state.options.bucketSize });
    state.rows = generated.rows;
    state.files = generated.files;
    state.buckets = generated.buckets;
    state.totalBytes = generated.bytes;
    state.directory = options.directory ?? await mkdtemp(join(tmpdir(), 's0-b10-bench-'));
    const keep = Boolean(options.directory);
    const env = await assemblePublic(state);
    const { ctx } = env;
    const counters = instrument(ctx.database);
    let reservation, session;
    try {
        reservation = await ctx.executionFence.reserve({ id: 'b10-bench' });
        const runTx = (operationId, body) => ctx.database.transaction(reservation.lease, publicTransactionRequest(operationId), body);
        const invocation = (actorId, momentId, operationId) => ({
            ctx, database: ctx.database, writeLease: reservation.lease, actorId, momentId, logicalTime: 1,
            runId: 'b10-bench', generation: 'g', operationId, signal: reservation.signal,
            transaction: (request, body) => ctx.database.transaction(reservation.lease, request, body)
        });
        const acquire = async (actorId, momentId, operationId) =>
            ctx.exploration.acquire({ actorId, momentId, query: null }, invocation(actorId, momentId, operationId));

        await seedActors(ctx, reservation, state.options);
        const grouping = new Map();
        for (const file of state.files) {
            const list = grouping.get(file.bucket);
            if (list) list.push(file);
            else grouping.set(file.bucket, [file]);
        }
        for (const bucket of [...grouping.keys()].sort()) ctx.public.registry.table(tableDecl(bucket));
        ctx.public.registry.table(largeDecl());
        ctx.public.registry.table(writesDecl());
        ctx.publicSelf.provide({
            path: '/self/profile.json', media: 'application/json',
            versions: async actorId => [{ owner: 'bench-self', version: actorId }],
            render: async function* (actorId, _view, signal) {
                signal.throwIfAborted();
                yield Buffer.from(JSON.stringify({ actor: actorId, kind: 'bench' }) + '\n');
            }
        });

        const seedStarted = performance.now();
        for (const bucket of [...grouping.keys()].sort()) {
            const rows = grouping.get(bucket).flatMap(file => file.rows);
            await runTx('b10-seed-' + bucket, tx => ctx.public.writer(tx).upsert(tablePath(bucket), rows));
        }
        const largeRows = Array.from({ length: 10000 }, (_, index) => ({
            file: 'large', id: 'large-' + String(index).padStart(6, '0'), kind: 'price',
            value: (index * 17) % 1000, note: index % 997 === 0 ? 'needle-large' : 'value-large'
        }));
        await runTx('b10-seed-large', tx => ctx.public.writer(tx).upsert(PUBLIC_LARGE, largeRows));
        const seedMs = performance.now() - seedStarted;

        const openMoment = (moment) => runTx('b10-open-' + moment, tx => ctx.momentViews.open(tx, 'm' + moment));
        await openMoment(1);
        session = await acquire('a', 'm1', 'b10-session');
        const sessionValue = () => session.value;

        const pageRequest = (maxItems, maxResponseBytes) => ({ maxItems, maxResponseBytes });
        const operations = {};
        const resetSnapshot = () => { counters.snapshotOpens = 0; counters.snapshotBudgetErrors = 0; };
        const pathAt = index => state.files[Math.floor(index) % state.files.length].path;
        const bucketAt = index => state.files[Math.floor(index) % state.files.length].bucket;
        const poolBucket = state.files[Math.floor(state.files.length / 2)].bucket;

        // 聚焦单项操作的 profile 模式：只跑测量循环，不跑历史/并发/共享。
        if (options.focus) {
            const focus = String(options.focus);
            const iterations = options.focusIterations ?? 30;
            const profilePath = options.profilePath ?? join(state.directory, 'profiles', focus + '.cpuprofile');
            const readOnce = async index => sessionValue().withView(async view => {
                const node = await view.stat(pathAt(index));
                const handle = await view.open(node);
                try { for await (const _ of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)); }
                finally { await handle.release(); }
            });
            let action;
            if (focus === 'ls') action = () => sessionValue().withView(view => view.list(tablePath(poolBucket), pageRequest(100, 65536)));
            else if (focus === 'read') {
                for (let index = 0; index < 20; index++) await readOnce(index);
                action = index => readOnce(index);
            }
            else if (focus === 'find') action = () => sessionValue().find({ root: PUBLIC_FILES, glob: '*.jsonl', page: pageRequest(state.options.files + 16, 134217728) });
            else if (focus === 'cold') action = () => sessionValue().withView(async view => {
                const node = await view.stat(PUBLIC_LARGE + '/large.jsonl');
                const handle = await view.open(node);
                try { for await (const _ of handle.value.read({ offset: '0', length: 65536, maxBytes: 65536 }, reservation.signal)); }
                finally { await handle.release(); }
            });
            else if (focus === 'concurrent') {
                const profilerHandle = profiler();
                const started = performance.now();
                await profilerHandle.start();
                const sessions = await Promise.all(Array.from({ length: state.options.concurrency }, (_, index) => acquire('a' + (index % state.options.actors), 'm1', 'b10-focus-concurrent-' + index)));
                await Promise.allSettled(sessions.map(async (lease, index) => {
                    const kind = index % 10;
                    if (kind < 5) await lease.value.withView(async view => {
                        const node = await view.stat(pathAt(index));
                        const handle = await view.open(node);
                        try { for await (const _ of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)); }
                        finally { await handle.release(); }
                    });
                    else if (kind < 8) await lease.value.withView(view => view.stat(pathAt(index)));
                    else if (kind < 9) await lease.value.withView(view => view.find({ root: tablePath(state.files[index % state.files.length].bucket), glob: '*.jsonl', page: pageRequest(128, 4194304) }));
                    else await lease.value.grep({ root: tablePath(state.files[index % state.files.length].bucket), pattern: 'needle', mode: 'literal', page: pageRequest(128, 4194304) });
                }));
                const elapsed = performance.now() - started;
                await Promise.allSettled(sessions.map(lease => lease.release()));
                await profilerHandle.stop(profilePath);
                await session.release();
                session = undefined;
                return {
                    schemaVersion: 1, mode: 'public-focus', focus, iterations: sessions.length, elapsedMs: elapsed,
                    perIterationMs: elapsed / sessions.length, profilePath, directory: state.directory,
                    loadAverage: loadavg(), memory: { peakRssBytes: memory.stop(), maxRssBytes: process.resourceUsage().maxRSS * 1024 }
                };
            }
            else throw Error('BENCH_FOCUS ' + focus);
            const profilerHandle = profiler();
            const started = performance.now();
            await profilerHandle.start();
            const count = focus === 'cold' ? 1 : iterations;
            for (let index = 0; index < count; index++) await action(index);
            const elapsed = performance.now() - started;
            await profilerHandle.stop(profilePath);
            await session.release();
            session = undefined;
            return {
                schemaVersion: 1, mode: 'public-focus', focus, iterations: count, elapsedMs: elapsed,
                perIterationMs: elapsed / count, profilePath, directory: state.directory,
                loadAverage: loadavg(), memory: { peakRssBytes: memory.stop(), maxRssBytes: process.resourceUsage().maxRSS * 1024 }
            };
        }

        // 冷渲染：只含 all 的 1 万行文件，缓存为空时首次读取。
        const largePath = PUBLIC_LARGE + '/large.jsonl';
        let coldRender;
        {
            const started = performance.now();
            await sessionValue().withView(async view => {
                const node = await view.stat(largePath);
                const handle = await view.open(node);
                try {
                    let read = 0;
                    for await (const chunk of handle.value.read({ offset: '0', length: 65536, maxBytes: 65536 }, reservation.signal)) read += chunk.length;
                    if (read <= 0) throw Error('BENCH_COLD_EMPTY');
                }
                finally { await handle.release(); }
            });
            const node = await sessionValue().stat(largePath);
            coldRender = { ms: performance.now() - started, bytes: Number(node.rawBytes), rows: 10000 };
        }

        // 热缓存：先按样本数预读，再计时。
        for (let index = 0; index < state.options.samples.read; index++) {
            await sessionValue().withView(async view => {
                const node = await view.stat(pathAt(index));
                const handle = await view.open(node);
                try { for await (const _ of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)); }
                finally { await handle.release(); }
            });
        }
        await sessionValue().withView(view => view.list(tablePath(poolBucket), pageRequest(100, 65536)));

        resetSnapshot();
        operations.ls = { ...await measure(state.options.samples.ls, async () => { const listed = await sessionValue().withView(view => view.list(tablePath(poolBucket), pageRequest(100, 65536))); if (listed.items.length < 1) throw Error('BENCH_LS_EMPTY'); }), extra: { items: Math.min(100, state.options.bucketSize), dir: tablePath(poolBucket) } };
        operations.ls.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.stat = { ...await measure(state.options.samples.stat, index => sessionValue().withView(view => view.stat(pathAt(index)))), extra: {} };
        operations.stat.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.read = { ...await measure(state.options.samples.read, async index => {
            await sessionValue().withView(async view => {
                const node = await view.stat(pathAt(index));
                const handle = await view.open(node);
                try {
                    let read = 0;
                    for await (const chunk of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)) read += chunk.length;
                    if (read <= 0) throw Error('BENCH_READ_EMPTY');
                }
                finally { await handle.release(); }
            });
        }), extra: {} };
        operations.read.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.find = { ...await measure(state.options.samples.find, () => sessionValue().find({ root: PUBLIC_FILES, glob: '*.jsonl', page: pageRequest(state.options.files + 16, 134217728) })), extra: {} };
        operations.find.extra.nodes = state.options.files;
        operations.find.extra.scopeFiles = state.options.files;
        operations.find.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        const rgBefore = searchProcesses.started;
        // 先跑一次冷 grep（缓存未热），再计时热 grep。
        const coldGrepStarted = performance.now();
        const coldGrep = await sessionValue().grep({ root: PUBLIC_FILES, pattern: 'needle', mode: 'literal', page: pageRequest(state.options.files + 16, 134217728) });
        const coldGrepMs = performance.now() - coldGrepStarted;
        operations.grep = { ...await measure(state.options.samples.grep, () => sessionValue().grep({ root: PUBLIC_FILES, pattern: 'needle', mode: 'literal', page: pageRequest(state.options.files + 16, 134217728) })), extra: { coldMs: coldGrepMs, coldMatches: coldGrep.items.length } };
        operations.grep.extra.rgProcesses = searchProcesses.started - rgBefore;
        operations.grep.extra.scopeFiles = state.options.files;
        operations.grep.snapshotOpens = counters.snapshotOpens;
        const regexStarted = performance.now();
        const regexGrep = await sessionValue().grep({ root: PUBLIC_FILES, pattern: 'n[a-z]edle', mode: 'regex', page: pageRequest(state.options.files + 16, 134217728) });
        operations.grep.extra.regexMs = performance.now() - regexStarted;
        operations.grep.extra.regexMatches = regexGrep.items.length;

        resetSnapshot();
        await ctx.explorationShell.execute('true', sessionValue(), invocation('a', 'm1', 'b10-bash-warm'));
        operations.bashStart = { ...await measure(state.options.samples.bashStart, () => ctx.explorationShell.execute('true', sessionValue(), invocation('a', 'm1', 'b10-bash'))), extra: { warmup: true } };
        operations.bashStart.snapshotOpens = counters.snapshotOpens;

        resetSnapshot();
        operations.selfRead = { ...await measure(state.options.samples.selfRead, () => sessionValue().withView(async view => {
            const node = await view.stat('/self/profile.json');
            const handle = await view.open(node);
            try { for await (const _ of handle.value.read({ offset: '0', length: Math.max(1, Number(node.rawBytes)), maxBytes: 1024 }, reservation.signal)); }
            finally { await handle.release(); }
        })), extra: {} };
        operations.selfRead.snapshotOpens = counters.snapshotOpens;

        // 写：1 事务 upsert 1000 行（含 FTS）。
        const writeRows = Array.from({ length: 1000 }, (_, index) => ({ file: 'writes', id: 'w' + String(index).padStart(4, '0'), kind: 'price', value: index, note: 'w0' }));
        const writeValues = [];
        for (let sample = 0; sample < 3; sample++) {
            // 每次写入一个空分片，测的是 upsert 1000 行的插入路径（含 FTS）。
            const rows = writeRows.map(row => ({ ...row, file: 'writes-' + sample, note: 'w' + sample }));
            const started = performance.now();
            await runTx('b10-write-' + sample, tx => ctx.public.writer(tx).upsert(PUBLIC_WRITES, rows));
            writeValues.push(performance.now() - started);
        }
        const write1000 = { samples: writeValues.length, ms: percentile(writeValues, 50), minMs: Math.min(...writeValues), p50Ms: percentile(writeValues, 50), maxMs: Math.max(...writeValues) };

        // 历史增长：每个 moment 变更 1% 文件（默认 100 个），同事务内记录 R_m。
        const history = { moments: state.options.moments, changeRatio: state.options.changeRatio, checkpoints: [] };
        const checkpoints = new Set(state.options.checkpoints.filter(moment => moment >= 1 && moment <= state.options.moments));
        const changeCount = Math.max(1, Math.round(state.options.files * state.options.changeRatio));
        const checkpointSamples = { ls: 10, stat: 20, read: 10, find: 2, grep: 1 };
        for (let moment = 1; moment <= state.options.moments; moment++) {
            await runTx('b10-moment-' + moment, async tx => {
                await ctx.momentViews.open(tx, 'm' + moment);
                const changed = new Set();
                for (let index = 0; index < changeCount; index++)
                    changed.add((moment * 97 + index * 131) % state.options.files);
                const grouped = new Map();
                for (const fileIndex of changed) {
                    const file = state.files[fileIndex];
                    const list = grouped.get(file.bucket);
                    const rows = file.rows.map(row => ({ ...row, note: 'moment-' + moment }));
                    if (list) list.push(...rows);
                    else grouped.set(file.bucket, rows);
                }
                for (const bucket of grouped.keys())
                    await ctx.public.writer(tx).upsert(tablePath(bucket), grouped.get(bucket));
            });
            if (checkpoints.has(moment)) {
                const pointSession = await acquire('a', 'm' + moment, 'b10-history-' + moment);
                try {
                    // 每次先热一次 grep，再计时，避免把该 moment 首次渲染算进退化的主指标。
                    await pointSession.value.grep({ root: PUBLIC_FILES, pattern: 'needle', mode: 'literal', page: pageRequest(state.options.files + 16, 134217728) });
                    const point = {
                        moment,
                        operations: {
                            ls: await measure(checkpointSamples.ls, () => pointSession.value.withView(view => view.list(tablePath(poolBucket), pageRequest(100, 65536)))),
                            stat: await measure(checkpointSamples.stat, index => pointSession.value.withView(view => view.stat(pathAt(index * 37)))),
                            read: await measure(checkpointSamples.read, async index => pointSession.value.withView(async view => {
                                const node = await view.stat(pathAt(index * 53));
                                const handle = await view.open(node);
                                try { for await (const _ of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)); }
                                finally { await handle.release(); }
                            })),
                            find: await measure(checkpointSamples.find, () => pointSession.value.find({ root: PUBLIC_FILES, glob: '*.jsonl', page: pageRequest(state.options.files + 16, 134217728) })),
                            grep: await measure(checkpointSamples.grep, () => pointSession.value.grep({ root: PUBLIC_FILES, pattern: 'needle', mode: 'literal', page: pageRequest(state.options.files + 16, 134217728) }))
                        },
                        databaseBytes: await databaseBytes(env.run)
                    };
                    history.checkpoints.push(point);
                }
                finally { await pointSession.release(); }
            }
        }

        // 1000 个 agent 读同一文件：只渲染一次。
        const sharePath = state.files[state.files.length - 1].path;
        const shareBefore = ctx.public.diagnostics().cache.renders;
        const shareStarted = performance.now();
        for (let index = 0; index < state.options.shareAgents; index++) {
            const lease = await acquire('a' + index, 'm' + state.options.moments, 'b10-share-' + index);
            try {
                await lease.value.withView(async view => {
                    const node = await view.stat(sharePath);
                    const handle = await view.open(node);
                    try { for await (const _ of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)); }
                    finally { await handle.release(); }
                });
            }
            finally { await lease.release(); }
        }
        const shareAfter = ctx.public.diagnostics().cache.renders;
        const share = { agents: state.options.shareAgents, path: sharePath, renders: shareAfter - shareBefore, ms: performance.now() - shareStarted };

        // 200 个并发会话：混合 stat/read/find/grep。
        const concurrentMoment = 'm' + state.options.moments;
        resetSnapshot();
        counters.readerQueueWaitMs = 0;
        const sessions = await Promise.all(Array.from({ length: state.options.concurrency }, (_, index) => acquire('a' + (index % state.options.actors), concurrentMoment, 'b10-concurrent-session-' + index)));
        const concurrentStarted = performance.now();
        const concurrentResults = await Promise.allSettled(sessions.map(async (lease, index) => {
            const started = performance.now();
            const kind = index % 10;
            try {
                if (kind < 5) {
                    await lease.value.withView(async view => {
                        const node = await view.stat(pathAt(index));
                        const handle = await view.open(node);
                        try { for await (const _ of handle.value.read({ offset: '0', length: Math.min(65536, Number(node.rawBytes)), maxBytes: 65536 }, reservation.signal)); }
                        finally { await handle.release(); }
                    });
                }
                else if (kind < 8) await lease.value.withView(view => view.stat(pathAt(index)));
                else if (kind < 9) await lease.value.withView(view => view.find({ root: tablePath(bucketAt(index)), glob: '**/*.jsonl', page: pageRequest(128, 4194304) }));
                else await lease.value.grep({ root: tablePath(bucketAt(index)), pattern: 'needle', mode: 'literal', page: pageRequest(128, 4194304) });
            }
            finally { /* 由外层统一 release */ }
            return performance.now() - started;
        }));
        const concurrentWall = performance.now() - concurrentStarted;
        const latencies = concurrentResults.filter(result => result.status === 'fulfilled').map(result => result.value);
        const errors = concurrentResults.filter(result => result.status === 'rejected');
        operations.concurrent = {
            ...summary(latencies, errors.length),
            extra: {
                wallMs: concurrentWall,
                throughputPerSecond: state.options.concurrency / (concurrentWall / 1000),
                sqliteSnapshotBudgetErrors: counters.snapshotBudgetErrors,
                readerQueueWaitMs: counters.readerQueueWaitMs,
                mix: { read: 50, stat: 30, find: 10, grep: 10 },
                ...(errors.length ? { failures: errors.slice(0, 3).map(result => String(result.reason?.message ?? result.reason)) } : {})
            }
        };
        operations.concurrent.snapshotOpens = counters.snapshotOpens;
        await Promise.allSettled(sessions.map(lease => lease.release()));
        await session.release();
        session = undefined;

        const diagnostics = ctx.public.diagnostics();
        const cache = diagnostics.cache;
        const database = await databaseBytes(env.run);
        const peakRss = memory.stop();
        const countersOut = {
            renderCount: cache.renders,
            cacheHitRate: cacheHitRate(cache),
            cache: { memoryHits: cache.memoryHits, diskHits: cache.diskHits, misses: cache.misses, entries: cache.entries, bytes: cache.bytes },
            rgProcesses: operations.grep.extra.rgProcesses,
            snapshotOpens: Object.fromEntries(Object.entries(operations).map(([name, operation]) => [name, operation.snapshotOpens])),
            readerQueueWaitMs: counters.readerQueueWaitMs,
            sqliteSnapshotBudgetErrors: counters.snapshotBudgetErrors,
            searchStale: diagnostics.searchStale,
            fallbackScans: diagnostics.fallbackScans,
            readers: state.options.readerWorkers,
            maxSnapshots: state.options.maxSnapshots,
            snapshotLeaseMs: SNAPSHOT_LEASE_MS
        };
        const resource = process.resourceUsage();
        return {
            schemaVersion: 1,
            mode: 'public',
            generatedAt: new Date().toISOString(),
            scale: {
                actors: state.options.actors, files: state.options.files, rowsPerFile: state.options.rowsPerFile,
                buckets: state.buckets, bucketSize: state.options.bucketSize,
                totalRows: state.options.files * state.options.rowsPerFile,
                findScopeFiles: state.options.files, grepScopeFiles: state.options.files,
                totalBytes: state.totalBytes, concurrency: state.options.concurrency,
                moments: state.options.moments, changeRatio: state.options.changeRatio,
                checkpointMoments: state.options.checkpoints
            },
            operations,
            coldRender,
            write1000,
            share,
            history,
            database,
            memory: {
                peakRssBytes: peakRss,
                maxRssBytes: resource.maxRSS * 1024,
                loadAverage: loadavg(),
                rssSamples: memory.sampler.samples
            },
            counters: countersOut,
            durationMs: performance.now() - startedAt,
            seedMs,
            directory: state.directory,
            config: {
                readerWorkers: state.options.readerWorkers, maxSnapshots: state.options.maxSnapshots,
                shellTimeoutMs: state.options.shellTimeoutMs, snapshotLeaseMs: SNAPSHOT_LEASE_MS,
                snapshotLeaseSource: SNAPSHOT_LEASE_SOURCE
            }
        };
    }
    finally {
        try { await session?.release(); } catch {}
        try { await reservation?.release(); } catch {}
        await env.host.close();
        memory.stop();
        if (!keep) await rm(state.directory, { recursive: true, force: true });
    }
}

/**
 * 已知现象：bash 单次调用超过 30s 时快照租约先过期，后续 RPC 报 SQLITE_SNAPSHOT_EXPIRED。
 * 默认 shell timeoutMs=30000 与租约同为 30s；这里可把 shell 超时单独调大，隔离两个到期点。
 */
export async function runExpiryProbe(options = {}) {
    const directory = options.directory ?? await mkdtemp(join(tmpdir(), 's0-b10-expiry-'));
    const shellTimeoutMs = options.shellTimeoutMs ?? 120000;
    const command = options.command ?? 'sleep 32; cat /public/bench/files/b0000/f000000.jsonl';
    const generated = generateData({ files: options.files ?? 100, rowsPerFile: 4, bucketSize: 100 });
    const state = {
        id: 'b10-expiry', rows: generated.rows, files: generated.files, buckets: generated.buckets,
        totalBytes: generated.bytes, directory,
        options: { actors: 1, files: generated.files.length, rowsPerFile: 4, bucketSize: 100, concurrency: 1, readerWorkers: 2, maxSnapshots: 8, shellTimeoutMs, moments: 1 }
    };
    const env = await assemblePublic(state);
    const { ctx } = env;
    let reservation, session;
    try {
        reservation = await ctx.executionFence.reserve({ id: 'b10-expiry' });
        const runTx = (operationId, body) => ctx.database.transaction(reservation.lease, publicTransactionRequest(operationId), body);
        const invocation = (operationId) => ({
            ctx, database: ctx.database, writeLease: reservation.lease, actorId: 'a', momentId: 'm1', logicalTime: 1,
            runId: 'b10-bench', generation: 'g', operationId, signal: reservation.signal,
            transaction: (request, body) => ctx.database.transaction(reservation.lease, request, body)
        });
        const grouping = new Map();
        for (const file of state.files) grouping.set(file.bucket, [...(grouping.get(file.bucket) ?? []), file]);
        for (const bucket of grouping.keys()) ctx.public.registry.table(tableDecl(bucket));
        for (const bucket of grouping.keys())
            await runTx('b10-expiry-seed-' + bucket, tx => ctx.public.writer(tx).upsert(tablePath(bucket), grouping.get(bucket).flatMap(file => file.rows)));
        await runTx('b10-expiry-open', tx => ctx.momentViews.open(tx, 'm1'));
        session = await ctx.exploration.acquire({ actorId: 'a', momentId: 'm1', query: null }, invocation('b10-expiry-session'));
        const rpcErrors = [];
        for (const name of ['stat', 'list', 'open', 'find', 'grep']) {
            const original = session.value[name].bind(session.value);
            session.value[name] = async (...args) => {
                try { return await original(...args); }
                catch (error) {
                    rpcErrors.push({ operation: name, message: String(error?.message ?? error), code: error?.code ?? null });
                    throw error;
                }
            };
        }
        const started = performance.now();
        let outcome = { ok: true }, error;
        try {
            const result = await ctx.explorationShell.execute(command, session.value, invocation('b10-expiry-bash'));
            outcome = { ok: result.exitCode === 0, exitCode: result.exitCode, stderr: result.stderr.slice(0, 500) };
        }
        catch (caught) {
            error = { name: caught?.name, message: String(caught?.message ?? caught), code: caught?.code };
            outcome = { ok: false };
        }
        return {
            schemaVersion: 1, mode: 'public-expiry', generatedAt: new Date().toISOString(),
            shellTimeoutMs, snapshotLeaseMs: SNAPSHOT_LEASE_MS, snapshotLeaseSource: SNAPSHOT_LEASE_SOURCE,
            command, elapsedMs: performance.now() - started, outcome,
            ...(error ? { error } : {}),
            rpcErrors: rpcErrors.slice(0, 50),
            rpcErrorCodes: Object.fromEntries([...new Set(rpcErrors.map(item => item.code ?? item.message))].map(code => [code, rpcErrors.filter(item => (item.code ?? item.message) === code).length])),
            loadAverage: loadavg(), directory
        };
    }
    finally {
        try { await session?.release(); } catch {}
        try { await reservation?.release(); } catch {}
        await env.host.close();
        if (!options.directory) await rm(directory, { recursive: true, force: true });
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
function listArgument(argv, name, fallback) {
    const raw = argument(argv, name, undefined);
    if (raw === undefined) return fallback;
    return raw.split(',').map(value => Number(value)).filter(value => Number.isSafeInteger(value) && value >= 1);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    const argv = process.argv.slice(2);
    const mode = argument(argv, 'mode', 'current');
    const out = argument(argv, 'out', undefined);
    const directory = argument(argv, 'directory', undefined);
    if (argv.includes('--expiry-probe')) {
        const report = await runExpiryProbe({
            directory,
            shellTimeoutMs: numberArgument(argv, 'shell-timeout-ms', 120000),
            command: argument(argv, 'command', undefined),
            files: numberArgument(argv, 'files', 100)
        });
        if (out) {
            await mkdir(dirname(out), { recursive: true });
            await writeFile(out, JSON.stringify(report, null, 2) + '\n');
        }
        console.log(JSON.stringify(report));
    }
    else {
        const samples = numberArgument(argv, 'samples', undefined);
        const report = await runBench({
            mode,
            directory,
            actors: numberArgument(argv, 'actors', 1000),
            files: numberArgument(argv, 'files', 10000),
            rowsPerFile: numberArgument(argv, 'rows', 20),
            concurrency: numberArgument(argv, 'concurrency', 200),
            scopeFiles: numberArgument(argv, 'scope-files', 2000),
            moments: numberArgument(argv, 'moments', 100),
            changePercentPerMoment: numberArgument(argv, 'change-percent', 1),
            changeRatio: numberArgument(argv, 'change-percent', 1) / 100,
            checkpoints: listArgument(argv, 'checkpoints', [1, 10, 100]),
            readerWorkers: numberArgument(argv, 'reader-workers', 4),
            maxSnapshots: numberArgument(argv, 'max-snapshots', 16),
            shareAgents: numberArgument(argv, 'share-agents', undefined),
            shellTimeoutMs: numberArgument(argv, 'shell-timeout-ms', 30000),
            focus: argument(argv, 'focus', undefined),
            profilePath: argument(argv, 'profile-out', undefined),
            focusIterations: numberArgument(argv, 'focus-iterations', 30),
            samples: samples === undefined ? undefined : { ls: samples, stat: samples, read: samples, find: Math.max(1, Math.floor(samples / 4)), grep: 1, bashStart: samples, selfRead: samples }
        });
        if (out) {
            await mkdir(dirname(out), { recursive: true });
            await writeFile(out, JSON.stringify(report, null, 2) + '\n');
        }
        console.log(JSON.stringify(report));
    }
}
