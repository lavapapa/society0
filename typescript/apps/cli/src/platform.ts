import { mkdir, readdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { basename, dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHost, type BootEntry } from '@society0/host';
import { compareCodePoints } from '@society0/host/order';
import type { CompositionPlan, EntryPatch, PlannedEntry, ProfileProblem } from '@society0/host/profile-loader';
import type { Context } from '@society0/host';
import type {} from '@society0/host/run-directory';
import type {} from '@society0/core/run-controller';
import type {} from '@society0/core/attempts';
import type {} from '@society0/core/sqlite-history';
import type {} from '@society0/core/sqlite-bootstrap';
import type {} from '@society0/core/sqlite';
import type {} from '@society0/core/observation';

const HOST_BOOT = ['resources', 'tasks', 'composition-strategies', 'slot-strategies', 'module-resolver', 'config-resolvers', 'profile-loader', 'inspector', 'run-directory'];

export const CHECK_SCHEMA = 'society0.check/1';
export const RUN_SCHEMA = 'society0.run/1';
export const STATUS_SCHEMA = 'society0.run-status/1';
export const RESULT_SCHEMA = 'society0.run-result/1';
export const LOCK_SCHEMA = 'society0.lock/1';
export const ERROR_SCHEMA = 'society0.error/1';
const STATUS_FILE = 'entry-status.json';
const RESULT_FILE = 'entry-result.json';

export class PlatformError extends Error {
    constructor(readonly code: string, message: string, readonly hint: string) {
        super(message);
        this.name = 'PlatformError';
    }
}

export interface CheckReport {
    readonly schema: typeof CHECK_SCHEMA;
    readonly ok: boolean;
    readonly missing: readonly ProfileProblem[];
    readonly warnings: readonly string[];
    readonly resolved: {
        readonly plugins: readonly string[];
        readonly slots: Readonly<Record<string, readonly string[]>>;
    };
}

interface ProfileDocument {
    format?: unknown;
    lockfile?: unknown;
    [key: string]: unknown;
}
interface RunStatus {
    schema: typeof STATUS_SCHEMA;
    runId: string;
    dir: string;
    state: 'running' | 'paused' | 'completed' | 'failed';
    completedTicks: number;
    frontier: unknown;
    lastReason: string | null;
    pid?: number;
    updatedAt: string;
    profile: string;
    lock?: string;
    patches?: string;
    lineage: string;
    ticks?: number;
    base?: string;
    world?: string;
    credentialsEnv?: string;
}

export function platformHost(extra: BootEntry[] = [], base?: string) {
    const boot = HOST_BOOT.map(specifier => specifier === 'module-resolver' && base
        ? { specifier: '@society0/host/module-resolver', config: { baseURL: baseURLOf(base) } }
        : { specifier: '@society0/host/' + specifier });
    return createHost({ baseURL: import.meta.url, boot: [...boot, ...extra] });
}

export function baseURLOf(base: string): string {
    return pathToFileURL(join(resolve(base), 'package.json')).href;
}

function needsModelCredentials(profile: unknown): boolean {
    try { return JSON.stringify(profile).includes('@society0/core/model-'); }
    catch { return false; }
}

function modelSeeds(profile: unknown): BootEntry[] {
    return needsModelCredentials(profile)
        ? [{ specifier: '@society0/host/slots' }, { specifier: '@society0/core/model-credentials', config: { stores: [] } }]
        : [];
}

async function readJson<T>(path: string): Promise<T> {
    try { return JSON.parse(await readFile(path, 'utf8')) as T; }
    catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT')
            throw new PlatformError('FILE_NOT_FOUND', `找不到文件：${path}`, '检查路径是否正确');
        throw new PlatformError('INVALID_JSON', `无法解析 JSON：${path}`, error instanceof Error ? error.message : String(error));
    }
}

async function loadProfile(path: string): Promise<{ profile: ProfileDocument; url: URL }> {
    const url = pathToFileURL(resolve(path));
    return { profile: await readJson<ProfileDocument>(url.pathname), url };
}

function active(plan: CompositionPlan): PlannedEntry[] {
    return plan.flat.filter(entry => !entry.disabled && !entry.group);
}

function driverProviders(plan: CompositionPlan): Record<string, string[]> {
    const result: Record<string, string[]> = {};
    for (const entry of active(plan)) {
        if (!entry.name.startsWith('@society0/core/driver-'))
            continue;
        const configured = (entry.config as { name?: unknown }).name;
        const name = typeof configured === 'string' && configured
            ? configured
            : entry.name === '@society0/core/driver-rule' ? 'rule'
            : entry.name === '@society0/core/driver-llm' ? 'llm/v1'
            : undefined;
        if (!name)
            continue;
        const slot = 'core/drivers/' + name;
        (result[slot] ??= []).push(entry.id);
    }
    return result;
}

function walkStrings(value: unknown, key: string, visit: (text: string) => void): void {
    if (Array.isArray(value)) {
        for (const item of value)
            walkStrings(item, key, visit);
        return;
    }
    if (!value || typeof value !== 'object')
        return;
    for (const [name, item] of Object.entries(value)) {
        if (name === key && typeof item === 'string')
            visit(item);
        else
            walkStrings(item, key, visit);
    }
}

function slotProblems(plan: CompositionPlan): ProfileProblem[] {
    const provided = driverProviders(plan), problems: ProfileProblem[] = [];
    const known = new Set(Object.keys(provided));
    for (const entry of active(plan)) {
        if (entry.name.startsWith('@society0/core/driver-'))
            continue;
        const requested: string[] = [];
        walkStrings(entry.config, 'driver', value => requested.push('core/drivers/' + value));
        for (const slot of new Set(requested))
            if (!known.has(slot))
                problems.push({
                    kind: 'slot', id: slot, requiredBy: entry.id,
                    hint: `补上提供 ${slot} 的 driver 插件（例如 @society0/core/driver-llm 或 @society0/core/driver-rule），或改掉 ${entry.id} 里的 driver`
                });
    }
    return problems;
}

function credentialProblems(plan: CompositionPlan, provided = false): ProfileProblem[] {
    if (provided)
        return [];
    const registered = new Set<string>();
    for (const entry of active(plan)) {
        if (entry.name !== '@society0/core/model-credentials')
            continue;
        const stores = (entry.config as { stores?: unknown }).stores;
        if (!Array.isArray(stores))
            continue;
        for (const store of stores)
            if (store && typeof store === 'object' && typeof (store as { reference?: unknown }).reference === 'string')
                registered.add((store as { reference: string }).reference);
    }
    const problems: ProfileProblem[] = [];
    for (const entry of active(plan)) {
        if (entry.name === '@society0/core/model-credentials')
            continue;
        const requested: string[] = [];
        walkStrings(entry.config, 'credential', value => requested.push(value));
        for (const reference of new Set(requested))
            if (!registered.has(reference))
                problems.push({
                    kind: 'credential', id: reference, requiredBy: entry.id,
                    hint: `在 @society0/core/model-credentials 的 stores 里注册凭据名 ${reference}（只检查名称，不读取 key）`
                });
    }
    return problems;
}

function credentialReferences(value: unknown): string[] {
    const names = new Set<string>();
    walkStrings(value, 'credential', name => names.add(name));
    return [...names].sort(compareCodePoints);
}

export function readCredentialsEnv(name: string, environment: NodeJS.ProcessEnv = process.env): Record<string, unknown> {
    const raw = environment[name];
    if (raw === undefined)
        throw new PlatformError('CREDENTIALS_ENV_MISSING', `环境变量 ${name} 未设置`, `在启动 CLI 的父进程里设置 ${name}；值不会写入磁盘或日志`);
    let value: unknown;
    try { value = JSON.parse(raw); }
    catch (error) {
        throw new PlatformError('CREDENTIALS_ENV_INVALID', `环境变量 ${name} 不是合法 JSON`, error instanceof Error ? error.message : String(error));
    }
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new PlatformError('CREDENTIALS_ENV_INVALID', `环境变量 ${name} 必须是 JSON 对象`, '使用与 model-credentials 文件存储相同的结构：{"<providerId>": {...}}');
    return value as Record<string, unknown>;
}

interface MigrationInfo { owner: string; after: readonly string[]; requiredBy: string }
type MigrationValue = { owner?: unknown; after?: unknown };

function flag(plan: CompositionPlan, name: string): boolean {
    return active(plan).some(entry => entry.name === name);
}

async function importMigration(problems: ProfileProblem[], provider: string, load: () => Promise<MigrationValue>): Promise<MigrationInfo | undefined> {
    try {
        const value = await load();
        if (!value || typeof value.owner !== 'string' || !Array.isArray(value.after))
            throw Error('MIGRATION_SHAPE');
        return { owner: value.owner, after: value.after.filter((item): item is string => typeof item === 'string'), requiredBy: provider };
    }
    catch (error) {
        problems.push({ kind: 'migration', id: provider, requiredBy: provider, hint: `无法加载 ${provider} 的 migration：${error instanceof Error ? error.message : String(error)}`, detail: error instanceof Error ? error.message : String(error) });
        return;
    }
}

async function collectMigrations(plan: CompositionPlan, problems: ProfileProblem[]): Promise<MigrationInfo[]> {
    const result: MigrationInfo[] = [];
    const source = (entry: PlannedEntry) => entry.binding?.identity?.source ?? {};
    const isActive = (name: string) => flag(plan, name);
    const entryOf = (name: string) => active(plan).find(entry => entry.name === name);
    if (isActive('@society0/core/artifacts-local')) {
        const item = await importMigration(problems, 'artifacts-local', async () => (await import('@society0/core/artifacts-local')).artifactsMigration);
        if (item) result.push(item);
    }
    if (isActive('@society0/core/actors')) {
        const item = await importMigration(problems, 'actors', async () => (await import('@society0/core/actors')).actorsMigration);
        if (item) result.push(item);
    }
    if (isActive('@society0/core/results')) {
        const entry = entryOf('@society0/core/results');
        const item = await importMigration(problems, 'results', async () => (await import('@society0/core/results')).resultsMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/thread')) {
        const entry = entryOf('@society0/core/thread');
        const item = await importMigration(problems, 'thread', async () => (await import('@society0/core/thread')).threadMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/actions')) {
        const entry = entryOf('@society0/core/actions');
        const item = await importMigration(problems, 'actions', async () => (await import('@society0/core/actions')).actionsMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/perception')) {
        const entry = entryOf('@society0/core/perception');
        const item = await importMigration(problems, 'perception', async () => (await import('@society0/core/perception')).perceptionMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/moment-views')) {
        const entry = entryOf('@society0/core/moment-views');
        const item = await importMigration(problems, 'moment-views', async () => (await import('@society0/core/moment-views')).momentViewsMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/public')) {
        const entry = entryOf('@society0/core/public');
        const item = await importMigration(problems, 'public', async () => (await import('@society0/core/public')).publicMigration(source(entry!)));
        if (item) result.push(item);
        const index = await importMigration(problems, 'public-index', async () => (await import('@society0/core/public')).publicIndexMigration(source(entry!)));
        if (index) result.push(index);
    }
    if (isActive('@society0/extras/workspace')) {
        const entry = entryOf('@society0/extras/workspace');
        const item = await importMigration(problems, 'workspace', async () => (await import('@society0/extras/workspace')).workspaceMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/driver-llm')) {
        const entry = entryOf('@society0/core/driver-llm');
        const item = await importMigration(problems, 'actor-tools', async () => (await import('@society0/core/actor-tools')).actorToolsMigration(source(entry!)));
        if (item) result.push(item);
    }
    if (isActive('@society0/core/runtime-events')) {
        const item = await importMigration(problems, 'runtime-events', async () => (await import('@society0/core/runtime-events')).eventsMigration);
        if (item) result.push(item);
    }
    if (isActive('@society0/social/social-repository')) {
        const item = await importMigration(problems, 'social-repository', async () => (await import('@society0/social/social-repository')).socialMigration);
        if (item) result.push(item);
    }
    if (isActive('@society0/social/measurement-repository')) {
        const item = await importMigration(problems, 'measurement-repository', async () => (await import('@society0/social/measurement-repository')).measurementMigration);
        if (item) result.push(item);
    }
    return result;
}

function migrationProblems(migrations: readonly MigrationInfo[]): ProfileProblem[] {
    const owners = new Set(migrations.map(item => item.owner)), problems: ProfileProblem[] = [];
    for (const migration of migrations)
        for (const dependency of migration.after)
            if (!owners.has(dependency))
                problems.push({
                    kind: 'migration', id: dependency, requiredBy: migration.requiredBy,
                    hint: `补上提供 migration ${dependency} 的插件（${migration.requiredBy} 的 migration 声明依赖它）`
                });
    return problems;
}

async function actorProblems(plan: CompositionPlan): Promise<ProfileProblem[]> {
    const problems: ProfileProblem[] = [];
    const activeEntries = active(plan);
    const actors = activeEntries.find(entry => entry.name === '@society0/core/actors');
    const seed = actors && (actors.config as { seed?: unknown }).seed === true;
    const creators: { id: string; requiredBy: string }[] = [];
    if (seed)
        creators.push({ id: 'actors.seed', requiredBy: actors!.id });
    const plain = activeEntries.find(entry => entry.name === '@society0/core/plain-init');
    if (plain)
        creators.push({ id: 'plain.seed', requiredBy: plain.id });
    if (creators.length > 1)
        problems.push({
            kind: 'actor', id: 'actors.seed', requiredBy: creators.map(item => item.requiredBy).join(','),
            hint: `profile 同时启用了多个 actor 创建者（${creators.map(item => item.id).join(', ')}）；只保留 actors.seed，或去掉 @society0/core/actors 的 seed 配置`
        });
    const domain = activeEntries.find(entry => entry.name === '@society0/social/social-init');
    if (domain) {
        let after: readonly string[] = [];
        try {
            const value: unknown = (await import('@society0/social/social-init')).initializerAfter;
            if (Array.isArray(value))
                after = value.filter(item => typeof item === 'string');
        }
        catch (error) {
            problems.push({ kind: 'actor', id: 'social.seed', requiredBy: domain.id, hint: `无法读取 social-init 的 initializer 依赖：${error instanceof Error ? error.message : String(error)}` });
        }
        if (!after.includes('actors.seed'))
            problems.push({
                kind: 'actor', id: 'social.seed', requiredBy: domain.id,
                hint: 'social-init 的 social.seed 必须声明 after:["actors.seed"]，以便 actor 在领域成员写入前存在'
            });
        else if (!seed)
            problems.push({
                kind: 'actor', id: 'actors.seed', requiredBy: domain.id,
                hint: 'social.seed 依赖 actors.seed，但 profile 里的 @society0/core/actors 没有开启 seed；给 actors 配置 {"seed":true}'
            });
    }
    return problems;
}

export interface LockReport {
    readonly schema: typeof LOCK_SCHEMA;
    readonly lockfile: string;
    readonly packages: Readonly<Record<string, { version: string; gitCommit: string; dirty: boolean; patches: readonly string[]; hostAbi: string }>>;
}

function gitOutput(directory: string, args: string[]): string {
    const result = spawnSync('git', ['-C', directory, ...args], { encoding: 'utf8' });
    if (result.status !== 0)
        throw new PlatformError('LOCK_GIT', `无法读取 ${directory} 的 git 信息：${(result.stderr || result.stdout || String(result.error ?? '')).trim()}`, '本地插件包必须位于一个 git 仓库内，且至少有一次提交');
    return result.stdout.trim();
}

function sourceIdentity(directory: string): { gitCommit: string; dirty: boolean } | undefined {
    let identity: unknown;
    try { identity = JSON.parse(readFileSync(join(directory, 'SOURCE.json'), 'utf8')); }
    catch { return undefined; }
    const implementation = (identity as { implementation?: unknown } | null)?.implementation as { gitCommit?: unknown; dirty?: unknown } | undefined;
    if (!implementation || typeof implementation.gitCommit !== 'string' || typeof implementation.dirty !== 'boolean')
        return undefined;
    return { gitCommit: implementation.gitCommit, dirty: implementation.dirty };
}

function lockIdentity(directory: string): { gitCommit: string; dirty: boolean } {
    try {
        return { gitCommit: gitOutput(directory, ['rev-parse', 'HEAD']), dirty: gitOutput(directory, ['status', '--porcelain', '--untracked-files=normal']).length > 0 };
    }
    catch (error) {
        if (!(error instanceof PlatformError) || error.code !== 'LOCK_GIT')
            throw error;
        const recorded = sourceIdentity(directory);
        if (!recorded)
            throw error;
        return recorded;
    }
}

function sidecarPatches(directory: string, manifest: { society?: { plugin?: { metadata?: string } } }): readonly string[] {
    const path = manifest.society?.plugin?.metadata;
    if (!path)
        return [];
    for (const candidate of [join(directory, path), join(directory, 'generated/plugin.meta.json')]) {
        try {
            const metadata = JSON.parse(readFileSync(candidate, 'utf8')) as { source?: { patches?: unknown } };
            return Array.isArray(metadata.source?.patches) ? metadata.source.patches.filter((item): item is string => typeof item === 'string') : [];
        }
        catch { /* try the generated sidecar location */ }
    }
    return [];
}

export async function generateLock(options: { base: string; out: string; packages: readonly string[] }): Promise<LockReport> {
    if (!options.packages.length)
        throw new PlatformError('LOCK_PACKAGES', 'lock 至少需要一个包名', '例如 society0 lock --base <dir> --out <lock.json> @fixture/plugin');
    const { locate, readManifest } = await import('@society0/host/module-resolver');
    const baseURL = baseURLOf(options.base);
    const out = resolve(options.out);
    const packages: Record<string, { version: string; gitCommit: string; dirty: boolean; patches: readonly string[]; hostAbi: string }> = {};
    for (const name of new Set(options.packages)) {
        const directory = locate(name, baseURL);
        const manifest = readManifest(directory);
        const { gitCommit, dirty } = lockIdentity(directory);
        packages[name] = { version: manifest.version, gitCommit, dirty, patches: sidecarPatches(directory, manifest), hostAbi: 'society0/host/1' };
    }
    const value = { lockfile: pathToFileURL(out).href, packages };
    await mkdir(dirname(out), { recursive: true });
    await writeFile(out, JSON.stringify(value, null, 2) + '\n');
    return { schema: LOCK_SCHEMA, lockfile: value.lockfile, packages };
}

export async function checkProfile(options: {
    profile: string;
    lock?: string;
    patches?: string;
    base?: string;
    credentialsEnv?: string;
}): Promise<CheckReport> {
    const { profile, url } = await loadProfile(options.profile);
    if (options.lock)
        profile.lockfile = pathToFileURL(resolve(options.lock)).href;
    const patches = options.patches ? await readJson<EntryPatch[]>(resolve(options.patches)) : undefined;
    if (patches && !Array.isArray(patches))
        throw new PlatformError('PATCHES_ARRAY', '--patches 必须是 EntryPatch 数组', '传入 JSON 数组');
    if (options.credentialsEnv)
        readCredentialsEnv(options.credentialsEnv);
    const host = await platformHost(modelSeeds(profile), options.base);
    try {
        const analysis = await host.ctx.profileLoader.analyze(profile as never, { baseURL: url.href, cliPatches: patches });
        const missing: ProfileProblem[] = [...analysis.missing];
        missing.push(...slotProblems(analysis.plan), ...credentialProblems(analysis.plan, Boolean(options.credentialsEnv)));
        missing.push(...await actorProblems(analysis.plan));
        const migrations = await collectMigrations(analysis.plan, missing);
        missing.push(...migrationProblems(migrations));
        const unique = new Map<string, ProfileProblem>();
        for (const item of missing)
            unique.set(`${item.kind}\u0000${item.id}\u0000${item.requiredBy ?? ''}`, item);
        const plugins = [...new Set(active(analysis.plan).map(entry => entry.name))].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
        return {
            schema: CHECK_SCHEMA,
            ok: unique.size === 0,
            missing: [...unique.values()],
            warnings: analysis.warnings,
            resolved: { plugins, slots: driverProviders(analysis.plan) }
        };
    }
    finally {
        await host.close();
    }
}

export interface StartOptions {
    profile: string;
    dir: string;
    lineage?: string;
    ticks?: number;
    lock?: string;
    patches?: string;
    base?: string;
    world?: string;
    credentialsEnv?: string;
    detach: boolean;
    mode: 'start' | 'resume';
}

interface PreparedPlan {
    plan: CompositionPlan;
    profileUrl: string;
    profile: ProfileDocument;
    ticks?: number;
    dir: string;
    lineage: string;
    input: unknown;
}

function tickOverride(plan: CompositionPlan, ticks: number | undefined): EntryPatch[] {
    if (ticks === undefined)
        return [];
    if (!Number.isSafeInteger(ticks) || ticks < 1)
        throw new PlatformError('TICKS_RANGE', '--ticks 必须是正整数', '例如 --ticks 2');
    const entry = active(plan).find(candidate => candidate.name === '@society0/core/run-controller');
    if (!entry)
        throw new PlatformError('RUN_CONTROLLER_MISSING', '该 profile 没有 run-controller，无法限制 tick 数', '在 profile 里安装 @society0/core/run-controller');
    return [{ id: entry.id, config: { ...(entry.config as Record<string, unknown>), maxTicks: ticks } }];
}

export async function prepareRunPlan(options: StartOptions): Promise<PreparedPlan> {
    const { profile, url } = await loadProfile(options.profile);
    if (options.lock)
        profile.lockfile = pathToFileURL(resolve(options.lock)).href;
    const cliPatches = options.patches ? await readJson<EntryPatch[]>(resolve(options.patches)) : [];
    const host = await platformHost(modelSeeds(profile), options.base);
    let analysis;
    try {
        analysis = await host.ctx.profileLoader.analyze(profile as never, { baseURL: url.href, cliPatches });
    }
    finally {
        await host.close();
    }
    if (analysis.missing.length)
        throw new PlatformError('CHECK_FAILED', `profile 未通过装配检查：${analysis.missing.map(item => `${item.kind}:${item.id}`).join(', ')}`, '先运行 society0 check --profile <p> --json 查看缺失项');
    const entryOf = (id: string) => analysis.plan.flat.find(entry => entry.id === id);
    const directory = resolve(options.dir);
    const lineage = resolve(options.lineage ?? join(dirname(directory), 'lineages', basename(directory)));
    const overrides: EntryPatch[] = [];
    const sqlite = entryOf('sqlite');
    if (sqlite) overrides.push({ id: 'sqlite', config: { ...(sqlite.config as Record<string, unknown>), directory, runId: basename(directory) } });
    const artifacts = entryOf('artifacts-local');
    if (artifacts) overrides.push({ id: 'artifacts-local', config: { ...(artifacts.config as Record<string, unknown>), directory: join(directory, 'artifacts') } });
    const history = entryOf('sqlite-history');
    if (history) overrides.push({ id: 'sqlite-history', config: { ...(history.config as Record<string, unknown>), directory, runId: basename(directory) } });
    const attempts = entryOf('attempts');
    if (attempts) overrides.push({ id: 'attempts', config: { ...(attempts.config as Record<string, unknown>), directory: lineage, lineage: basename(lineage) } });
    overrides.push(...tickOverride(analysis.plan, options.ticks));
    const input = options.world && options.mode === 'start' ? await readJson<Record<string, unknown>>(resolve(options.world)) : {};
    if (!input || typeof input !== 'object' || Array.isArray(input))
        throw new PlatformError('WORLD_INVALID', '--world 必须是 JSON 对象', 'world 文件形如 {"actors":[{"id","attributes","source"}]}');
    const host2 = await platformHost(modelSeeds(profile), options.base);
    try {
        const plan = await host2.ctx.profileLoader.compile(profile as never, { baseURL: url.href, cliPatches: [...cliPatches, ...overrides] });
        return { plan, profileUrl: url.href, profile, ticks: options.ticks, dir: directory, lineage, input };
    }
    finally {
        await host2.close();
    }
}

async function writeJsonAtomic(path: string, value: unknown): Promise<void> {
    await mkdir(dirname(path), { recursive: true });
    const temporary = path + '.' + process.pid + '.tmp';
    await writeFile(temporary, JSON.stringify(value, null, 2) + '\n');
    await rename(temporary, path);
}

function statusPath(dir: string) { return join(resolve(dir), STATUS_FILE); }
function resultPath(dir: string) { return join(resolve(dir), RESULT_FILE); }

function alive(pid: number | undefined): boolean {
    if (!pid || !Number.isSafeInteger(pid) || pid <= 0)
        return false;
    try { process.kill(pid, 0); return true; }
    catch (error) { return (error as NodeJS.ErrnoException).code === 'EPERM'; }
}

export async function readStatus(dir: string): Promise<RunStatus> {
    let status: RunStatus;
    try { status = await readJson<RunStatus>(statusPath(dir)); }
    catch (error) {
        if (error instanceof PlatformError && error.code === 'FILE_NOT_FOUND')
            throw new PlatformError('RUN_NOT_FOUND', `run 目录没有 ${STATUS_FILE}：${resolve(dir)}`, '确认 --dir 指向 society0 run start 创建的目录');
        throw error;
    }
    if (status.state === 'running' && !alive(status.pid))
        return { ...status, state: 'paused', lastReason: status.lastReason ?? 'process-exited-without-status' };
    return status;
}

export async function runStatus(dir: string): Promise<Record<string, unknown>> {
    const status = await readStatus(dir);
    return {
        schema: STATUS_SCHEMA,
        runId: status.runId,
        dir: status.dir,
        state: status.state,
        completedTicks: status.completedTicks,
        frontier: status.frontier,
        lastReason: status.lastReason,
        ...(status.pid ? { pid: status.pid } : {}),
        updatedAt: status.updatedAt
    };
}

export async function runResult(dir: string): Promise<Record<string, unknown>> {
    try {
        const result = await readJson<Record<string, unknown>>(resultPath(dir));
        return result;
    }
    catch (error) {
        if (!(error instanceof PlatformError) || error.code !== 'FILE_NOT_FOUND')
            throw error;
    }
    const status = await readStatus(dir);
    if (status.state === 'completed')
        throw new PlatformError('RESULT_MISSING', `run 已完成但没有 ${RESULT_FILE}`, '检查 run 目录是否被外部清理');
    throw new PlatformError('RUN_NOT_COMPLETED', `run 当前状态是 ${status.state}，没有可用结果`, '先用 run status 查看进度，必要时 run resume');
}

export function spawnWorker(main: string, options: StartOptions): Promise<number> {
    return spawnDetached(main, ['__worker', '--mode', options.mode, '--profile', options.profile, '--dir', options.dir, ...(options.lineage ? ['--lineage', options.lineage] : []), ...(options.ticks !== undefined ? ['--ticks', String(options.ticks)] : []), ...(options.lock ? ['--lock', options.lock] : []), ...(options.patches ? ['--patches', options.patches] : []), ...(options.base ? ['--base', options.base] : []), ...(options.world ? ['--world', options.world] : []), ...(options.credentialsEnv ? ['--credentials-env', options.credentialsEnv] : [])], options.dir);
}

async function spawnDetached(main: string, args: string[], dir: string): Promise<number> {
    const { spawn } = await import('node:child_process');
    const { openSync, closeSync } = await import('node:fs');
    const log = openSync(join(dirname(resolve(dir)), basename(resolve(dir)) + '.log'), 'a');
    try {
        const child = spawn(process.execPath, [main, ...args], { detached: true, stdio: ['ignore', log, log] });
        child.unref();
        if (!child.pid)
            throw new PlatformError('SPAWN_FAILED', '无法启动后台 run 进程', '检查 node 可执行文件与权限');
        return child.pid;
    }
    finally {
        closeSync(log);
    }
}

export async function startRun(main: string, options: StartOptions): Promise<Record<string, unknown>> {
    options = {
        ...options,
        profile: options.profile ? resolve(options.profile) : options.profile,
        dir: resolve(options.dir),
        lineage: options.lineage ? resolve(options.lineage) : undefined,
        lock: options.lock ? resolve(options.lock) : undefined,
        patches: options.patches ? resolve(options.patches) : undefined,
        base: options.base ? resolve(options.base) : undefined,
        world: options.world ? resolve(options.world) : undefined
    };
    if (options.credentialsEnv)
        readCredentialsEnv(options.credentialsEnv);
    if (options.world && options.mode === 'start')
        await readJson<Record<string, unknown>>(resolve(options.world));
    await mkdir(dirname(options.dir), { recursive: true });
    if (options.mode === 'start')
        await requireFresh(resolve(options.dir), resolve(options.lineage ?? join(dirname(resolve(options.dir)), 'lineages', basename(resolve(options.dir)))));
    if (options.mode === 'resume') {
        const status = await readStatus(options.dir);
        if (status.state === 'running' && alive(status.pid))
            throw new PlatformError('RUN_ALREADY_RUNNING', `run 仍在运行（pid ${status.pid}）`, '先停止现有进程或等待它结束');
        if (status.state === 'completed')
            throw new PlatformError('RUN_COMPLETED', 'run 已完成，没有可续跑的 frontier', '用 run result 读取结果');
        options = { ...options, profile: status.profile, lineage: status.lineage, ticks: options.ticks ?? status.ticks, lock: options.lock ?? status.lock, patches: options.patches ?? status.patches, base: options.base ?? status.base, world: options.world ?? status.world, credentialsEnv: options.credentialsEnv ?? status.credentialsEnv };
    }
    const pid = await spawnWorker(main, options);
    const now = new Date().toISOString();
    const status: RunStatus = {
        schema: STATUS_SCHEMA,
        runId: basename(resolve(options.dir)),
        dir: resolve(options.dir),
        state: 'running',
        completedTicks: 0,
        frontier: null,
        lastReason: null,
        pid,
        updatedAt: now,
        profile: options.profile,
        lock: options.lock,
        patches: options.patches,
        lineage: options.lineage ?? join(dirname(resolve(options.dir)), 'lineages', basename(resolve(options.dir))),
        ticks: options.ticks,
        base: options.base,
        world: options.world,
        credentialsEnv: options.credentialsEnv
    };
    await writeJsonAtomic(statusPath(options.dir), status);
    return { schema: RUN_SCHEMA, runId: status.runId, pid, dir: status.dir, state: 'running' };
}

async function requireFresh(directory: string, lineage: string): Promise<void> {
    const exists = async (path: string) => {
        try { await stat(path); return true; }
        catch { return false; }
    };
    if (await exists(directory)) {
        const entries = await readdir(directory).catch(() => [] as string[]);
        // Detached start creates the status file before the worker boots.
        if (entries.some(name => name !== 'entry-status.json' && name !== 'entry-run.log'))
            throw new PlatformError('FRESH_RUN_EXISTS', `run 目录已存在：${directory}`, '换一个 --dir，或对已有 run 使用 run resume');
    }
    if (await exists(lineage))
        throw new PlatformError('FRESH_LINEAGE_EXISTS', `lineage 目录已存在：${lineage}`, '换一个 --lineage，或保留现有 lineage 供 resume 使用');
    await mkdir(dirname(directory), { recursive: true });
    await mkdir(dirname(lineage), { recursive: true });
}

export async function worker(options: StartOptions): Promise<number> {
    const directory = resolve(options.dir), lineage = resolve(options.lineage ?? join(dirname(directory), 'lineages', basename(directory)));
    const runId = basename(directory);
    const writeStatus = async (state: RunStatus['state'], completedTicks: number, frontier: unknown, lastReason: string | null, pid?: number) => {
        await writeJsonAtomic(statusPath(directory), {
            schema: STATUS_SCHEMA, runId, dir: directory, state, completedTicks, frontier, lastReason,
            ...(pid ? { pid } : {}), updatedAt: new Date().toISOString(), profile: options.profile, lock: options.lock, patches: options.patches, lineage, ticks: options.ticks,
            base: options.base, world: options.world, credentialsEnv: options.credentialsEnv
        } satisfies RunStatus);
    };
    if (options.mode === 'start') {
        await requireFresh(directory, lineage);
        await mkdir(lineage, { recursive: true });
    }
    await mkdir(directory, { recursive: true });
    await writeStatus('running', 0, null, null, process.pid);
    let host: Awaited<ReturnType<typeof createHost>> | undefined;
    let interval: ReturnType<typeof setInterval> | undefined;
    try {
        const prepared = await prepareRunPlan({ ...options, lineage });
        const seed: BootEntry[] = HOST_BOOT.map(specifier => specifier === 'module-resolver' && options.base
            ? { specifier: '@society0/host/module-resolver', config: { baseURL: baseURLOf(options.base) } }
            : { specifier: '@society0/host/' + specifier });
        const modelEnabled = prepared.plan.flat.some(entry => !entry.disabled && ['@society0/core/model-pi', '@society0/core/model-codex-subscription'].includes(entry.name));
        if (modelEnabled || options.credentialsEnv)
            seed.push({ specifier: '@society0/host/slots' }, { specifier: '@society0/core/model-credentials', config: { stores: [] } });
        host = await createHost({ baseURL: import.meta.url, boot: seed });
        if (options.credentialsEnv) {
            const values = readCredentialsEnv(options.credentialsEnv);
            const references = credentialReferences(prepared.profile);
            const service = host.ctx.get('modelCredentials', false) as { register(reference: string, store: unknown): void } | undefined;
            if (references.length && !service)
                throw new PlatformError('CREDENTIALS_UNAVAILABLE', 'profile 需要凭据，但没有装配 @society0/core/model-credentials', '在 profile 里加入 model-credentials');
            if (service && references.length) {
                const { MemoryCredentialStore } = await import('@society0/core/model-credentials');
                const store = new MemoryCredentialStore(values as never);
                for (const reference of references)
                    service.register(reference, store);
            }
        }
        const plan = prepared.plan;
        const entryOf = (id: string) => plan.flat.find(entry => entry.id === id);
        const migrationValues: unknown[] = [];
        const add = async (load: () => Promise<unknown>) => { migrationValues.push(await load()); };
        await add(async () => (await import('@society0/core/artifacts-local')).artifactsMigration);
        if (flag(plan, '@society0/core/actors')) await add(async () => (await import('@society0/core/actors')).actorsMigration);
        const source = (id: string) => entryOf(id)?.binding?.identity?.source ?? {};
        if (flag(plan, '@society0/core/results')) await add(async () => (await import('@society0/core/results')).resultsMigration(source('results')));
        if (flag(plan, '@society0/core/thread')) await add(async () => (await import('@society0/core/thread')).threadMigration(source('thread')));
        if (flag(plan, '@society0/core/actions')) await add(async () => (await import('@society0/core/actions')).actionsMigration(source('actions')));
        if (flag(plan, '@society0/core/perception')) await add(async () => (await import('@society0/core/perception')).perceptionMigration(source('perception')));
        if (flag(plan, '@society0/core/moment-views')) await add(async () => (await import('@society0/core/moment-views')).momentViewsMigration(source('moment-views')));
        if (flag(plan, '@society0/core/public')) {
            await add(async () => (await import('@society0/core/public')).publicMigration(source('public')));
            await add(async () => (await import('@society0/core/public')).publicIndexMigration(source('public')));
        }
        if (flag(plan, '@society0/extras/workspace')) await add(async () => (await import('@society0/extras/workspace')).workspaceMigration(source('workspace')));
        if (flag(plan, '@society0/core/driver-llm')) await add(async () => (await import('@society0/core/actor-tools')).actorToolsMigration(source('driver-llm')));
        if (flag(plan, '@society0/core/runtime-events')) await add(async () => (await import('@society0/core/runtime-events')).eventsMigration);
        if (flag(plan, '@society0/social/social-repository')) await add(async () => (await import('@society0/social/social-repository')).socialMigration);
        if (flag(plan, '@society0/social/measurement-repository')) await add(async () => (await import('@society0/social/measurement-repository')).measurementMigration);
        const databaseEntry = entryOf('sqlite');
        const historyEntry = entryOf('sqlite-history');
        const event = flag(plan, '@society0/core/runtime-events');
        let context: Context | undefined;
        const handle = await host.ctx.runDirectory.create({
            runId, generation: plan, mount: { runEntryId: 'run', bootstrapEntryId: 'bootstrap' },
            prepare: async (bootstrapCtx, phase) => {
                const consumer = await phase!.run.plugin({ name: 'platform.prepare', inject: ['attempts', 'sqliteBootstrap', 'history', 'database'], apply(value: Context) { context = value; } });
                await consumer.await();
                const ctx = context!;
                if (options.mode === 'resume')
                    await ctx.attempts.verifyAuthority();
                else
                    await ctx.attempts.initialize();
                let root;
                if (options.mode === 'resume')
                    root = await ctx.history.activate();
                else {
                    const frontier = { codec: event ? 'events/1' : 'staged/1', value: '0', logicalTime: event ? { time: '0', offset: '0' } : '0', completedCount: '0', pendingAcknowledgements: [] };
                    root = await ctx.sqliteBootstrap.create({
                        operationId: 'create:' + runId, runId, directory, input: prepared.input,
                        migrations: migrationValues as never,
                        initialFrontier: frontier,
                        initialControl: { generation: (databaseEntry!.config as { generation: string }).generation, controlSequence: (historyEntry!.config as { controlSequence: string }).controlSequence, planId: prepared.profileUrl },
                        requiredInitializerIds: [flag(plan, '@society0/social/social') ? 'social.seed' : 'plain.seed']
                    });
                }
                const status = await ctx.history.status();
                return { root, metadata: ctx.database.metadata, database: ctx.database, state: status.state };
            }
        });
        const lease = host.ctx.runDirectory.acquire(runId, 'write');
        let consumer;
        try {
            consumer = await lease.ctx.plugin({ name: 'platform.consume', inject: ['runs', 'observation', 'history', 'database', 'attempts'], apply(value: Context) { context = value; } });
            await consumer.await();
        }
        catch (error) {
            await lease.release();
            throw error;
        }
        const ctx = context!;
        interval = setInterval(() => {
            void (async () => {
                try {
                    const status = await ctx.history.status();
                    const frontier = status.completePoint?.frontier;
                    if (frontier)
                        await writeStatus('running', Number(frontier.completedCount), frontier, null, process.pid);
                }
                catch { /* status is best-effort while running */ }
            })();
        }, 200);
        interval.unref();
        const controller = new AbortController();
        const stop = () => controller.abort(new Error('SIGTERM'));
        process.once('SIGTERM', stop);
        process.once('SIGINT', stop);
        let result;
        try {
            result = await ctx.runs.run(controller.signal);
        }
        finally {
            process.off('SIGTERM', stop);
            process.off('SIGINT', stop);
        }
        if (interval)
            clearInterval(interval);
        const frontier = result.cut?.frontier ?? null;
        const completed = frontier ? Number(frontier.completedCount) : 0;
        await writeStatus(result.status, completed, frontier, result.reason ?? null, process.pid);
        await writeJsonAtomic(resultPath(directory), {
            schema: RESULT_SCHEMA, runId, dir: directory, state: result.status,
            result, activations: null,
            activationsNote: 'D1 尚未合入；activations 摘要字段保留为空'
        });
        await consumer.dispose();
        await lease.release();
        await handle.close();
        await host.close();
        return result.status === 'completed' ? 0 : 1;
    }
    catch (error) {
        if (interval)
            clearInterval(interval);
        const message = error instanceof Error ? error.message : String(error);
        try { await writeStatus('failed', 0, null, message); }
        catch { /* directory may not be writable */ }
        try { await host?.close(); }
        catch { /* preserve original failure */ }
        throw error;
    }
}
