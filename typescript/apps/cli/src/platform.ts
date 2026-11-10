import { mkdir, mkdtemp, readdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { basename, dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { createHost, type BootEntry } from '@society0/host';
import { compareCodePoints } from '@society0/host/order';
import type { CompositionPlan, EntryPatch, PlannedEntry, ProfileProblem } from '@society0/host/profile-loader';
import type { Context } from '@society0/host';
import type { RunHandle } from '@society0/host/run-directory';
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
    /** Owners actually collected from the plan, including plugin-provided migrations. */
    readonly migrations: readonly string[];
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

export interface WorldDocument {
    readonly actors?: readonly { readonly id: string; readonly attributes?: unknown; readonly source: unknown }[];
    readonly [key: string]: unknown;
}

/** Read and validate a world file the same way for check and run start. */
async function readWorld(path: string): Promise<WorldDocument> {
    const value = await readJson<unknown>(resolve(path));
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new PlatformError('WORLD_INVALID', '--world 必须是 JSON 对象', 'world 文件形如 {"actors":[{"id","attributes","source"}]}');
    const world = value as Record<string, unknown>;
    const actors = world.actors;
    if (actors !== undefined) {
        if (!Array.isArray(actors))
            throw new PlatformError('WORLD_INVALID', '--world 的 actors 必须是数组', 'world 文件形如 {"actors":[{"id","attributes","source"}]}');
        for (const [index, actor] of actors.entries()) {
            if (!actor || typeof actor !== 'object' || Array.isArray(actor) || typeof (actor as { id?: unknown }).id !== 'string' || !(actor as { id: string }).id)
                throw new PlatformError('WORLD_ACTOR_INVALID', `--world 的 actors[${index}] 缺少非空字符串 id`, 'world 的 actors 段每条都要有 id 和 source');
            const source = (actor as { source?: unknown }).source;
            if (source === undefined || source === null || source === '')
                throw new PlatformError('WORLD_ACTOR_SOURCE_MISSING', `--world 的 actors[${index}].source 缺失（主体 ${(actor as { id: string }).id}）`, 'world 的 actors 段每条都要有 id 和 source');
        }
    }
    return world as WorldDocument;
}

function active(plan: CompositionPlan): PlannedEntry[] {
    return plan.flat.filter(entry => !entry.disabled && !entry.group);
}

export interface SlotGraph {
    readonly provides: Readonly<Record<string, readonly string[]>>;
    readonly requires: readonly { readonly slot: string; readonly requiredBy: string }[];
}

function configValue(config: unknown, path: string): unknown {
    let value = config;
    for (const part of path.split('.')) {
        if (!value || typeof value !== 'object' || Array.isArray(value))
            return undefined;
        value = (value as Record<string, unknown>)[part];
    }
    return value;
}

/** Replace `{configKey}` placeholders with the entry's merged config value. */
function expandSlot(pattern: string, config: unknown): string {
    return pattern.replace(/\{([^{}]+)\}/g, (_, key: string) => {
        const value = configValue(config, key.trim());
        return value === undefined || value === null ? `{${key}}` : String(value);
    });
}

/** Static slot graph derived from each active entry's declared slots and merged config. */
export function slotGraph(plan: CompositionPlan): SlotGraph {
    const provides: Record<string, string[]> = {};
    const requires: { slot: string; requiredBy: string }[] = [];
    for (const entry of active(plan)) {
        const slots = entry.binding?.api.slots;
        if (!slots)
            continue;
        for (const pattern of slots.provides ?? []) {
            const slot = expandSlot(pattern, entry.config);
            (provides[slot] ??= []).push(entry.id);
        }
        for (const pattern of slots.requires ?? [])
            requires.push({ slot: expandSlot(pattern, entry.config), requiredBy: entry.id });
    }
    return { provides, requires };
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

function slotProblems(plan: CompositionPlan, installed: ReadonlyMap<string, readonly string[]> = new Map()): ProfileProblem[] {
    const graph = slotGraph(plan), provided = new Set(Object.keys(graph.provides)), problems: ProfileProblem[] = [];
    const hint = (slot: string, requiredBy: string) => {
        const packages = installed.get(slot);
        return packages?.length
            ? `槽位 ${slot} 没有任何活动条目提供；已安装的 ${packages.join(', ')} 声明提供该槽位，把对应条目加入 profile（${requiredBy} 需要它）`
            : `补上提供 ${slot} 的插件，或改掉 ${requiredBy} 里的引用`;
    };
    const requested = new Map<string, Set<string>>();
    for (const item of graph.requires) {
        if (provided.has(item.slot))
            continue;
        (requested.get(item.slot) ?? requested.set(item.slot, new Set()).get(item.slot)!).add(item.requiredBy);
    }
    for (const entry of active(plan))
        walkStrings(entry.config, 'driver', value => {
            const slot = 'core/drivers/' + value;
            if (!provided.has(slot))
                (requested.get(slot) ?? requested.set(slot, new Set()).get(slot)!).add(entry.id);
        });
    for (const [slot, owners] of requested)
        for (const requiredBy of owners)
            problems.push({ kind: 'slot', code: 'SLOT_PROVIDER_MISSING', id: slot, requiredBy, hint: hint(slot, requiredBy) });
    return problems;
}

/** Best-effort package discovery for `slot` hints: read installed sidecars without importing plugins. */
async function installedSlotProviders(profile: ProfileDocument, profileUrl: URL, base: string | undefined, slots: ReadonlySet<string>): Promise<Map<string, string[]>> {
    const result = new Map<string, string[]>();
    if (!slots.size || typeof profile.lockfile !== 'string')
        return result;
    let lock: { packages?: Record<string, unknown> };
    try { lock = JSON.parse(await readFile(new URL(profile.lockfile, profileUrl), 'utf8')) as typeof lock; }
    catch { return result; }
    const { locate, readManifest } = await import('@society0/host/module-resolver');
    const baseURL = base ? baseURLOf(base) : import.meta.url;
    for (const name of Object.keys(lock.packages ?? {})) {
        try {
            const directory = locate(name, baseURL);
            const manifest = readManifest(directory);
            const apiPath = manifest.society?.plugin?.api;
            if (!apiPath)
                continue;
            const target = typeof manifest.exports === 'object' && manifest.exports ? (manifest.exports as Record<string, unknown>)[apiPath] : undefined;
            const file = typeof target === 'string' ? target : target && typeof target === 'object' ? (target as { default?: unknown }).default : undefined;
            const apiFile = typeof file === 'string' ? file : apiPath;
            const api = JSON.parse(await readFile(join(directory, apiFile), 'utf8')) as { entries?: readonly { exportPath?: string; slots?: { provides?: readonly string[] } }[] };
            for (const entry of api.entries ?? []) {
                for (const pattern of entry.slots?.provides ?? []) {
                    if (pattern.includes('{') || !slots.has(pattern))
                        continue;
                    const specifier = name + (entry.exportPath && entry.exportPath !== '.' ? entry.exportPath.slice(1) : '');
                    const owners = result.get(pattern) ?? [];
                    if (!owners.includes(specifier))
                        owners.push(specifier);
                    result.set(pattern, owners);
                }
            }
        }
        catch { /* package may be lock-only; a missing hint is allowed */ }
    }
    return result;
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
interface CollectedMigration extends MigrationInfo { value: MigrationValue }
type MigrationValue = { owner?: unknown; from?: unknown; to?: unknown; after?: unknown };

function flag(plan: CompositionPlan, name: string): boolean {
    return active(plan).some(entry => entry.name === name);
}

function canonical(value: unknown): string {
    if (Array.isArray(value))
        return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object')
        return '{' + Object.keys(value as Record<string, unknown>).sort(compareCodePoints)
            .map(key => JSON.stringify(key) + ':' + canonical((value as Record<string, unknown>)[key])).join(',') + '}';
    return JSON.stringify(value) ?? 'undefined';
}

function migrationKey(owner: string, from: number, to: number): string {
    return `${owner}\u0000${from}\u0000${to}`;
}

interface ParsedMigration {
    owner: string;
    from: number;
    to: number;
    after: readonly string[];
    value: MigrationValue;
}

/** Validate one element of a plugin's ``migrations`` export against the SqliteMigration shape. */
function parsePluginMigration(raw: unknown): ParsedMigration {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw))
        throw Error('migration 元素必须是对象');
    const value = raw as Record<string, unknown>;
    if (typeof value.owner !== 'string' || !value.owner)
        throw Error('owner 必须是非空字符串');
    if (!Number.isSafeInteger(value.from) || (value.from as number) < 0)
        throw Error('from 必须是非负整数');
    if (!Number.isSafeInteger(value.to) || (value.to as number) <= (value.from as number))
        throw Error('to 必须是大于 from 的整数');
    if (!Array.isArray(value.after) || value.after.some(item => typeof item !== 'string'))
        throw Error('after 必须是字符串数组');
    if (!value.source || typeof value.source !== 'object' || Array.isArray(value.source) || !Object.keys(value.source).length)
        throw Error('source 必须是非空对象');
    if (!Array.isArray(value.statements) || value.statements.some(item => !item || typeof item !== 'object' || typeof (item as { sql?: unknown }).sql !== 'string'))
        throw Error('statements 必须是包含 sql 字符串的对象数组');
    return {
        owner: value.owner,
        from: value.from as number,
        to: value.to as number,
        after: value.after as string[],
        value: value as MigrationValue
    };
}

async function collectMigrations(plan: CompositionPlan, problems: ProfileProblem[]): Promise<CollectedMigration[]> {
    const result: CollectedMigration[] = [];
    const source = (entry: PlannedEntry) => entry.binding?.identity?.source ?? {};
    const seen = new Map<string, { value: MigrationValue; origin: string; id: string }>();
    for (const item of result)
        if (typeof item.value.owner === 'string' && typeof item.value.from === 'number' && typeof item.value.to === 'number')
            seen.set(migrationKey(item.value.owner, item.value.from, item.value.to), { value: item.value, origin: item.requiredBy, id: item.requiredBy });
    for (const entry of active(plan)) {
        const url = entry.binding?.identity?.url;
        if (!url)
            continue;
        const origin = `${entry.name} (${entry.id})`;
        let namespace: Record<string, unknown>;
        try { namespace = await import(url) as Record<string, unknown>; }
        catch (error) {
            problems.push({
                kind: 'migration', id: entry.name, requiredBy: entry.id,
                hint: `无法加载 ${origin} 的 migration：${error instanceof Error ? error.message : String(error)}`,
                detail: error instanceof Error ? error.message : String(error)
            });
            continue;
        }
        if (!('migrations' in namespace))
            continue;
        let exported: unknown;
        try {
            exported = (namespace as { migrations?: unknown }).migrations;
            exported = typeof exported === 'function' ? await exported(source(entry)) : exported;
            if (!Array.isArray(exported))
                throw Error('migrations 导出必须是数组或返回数组的函数');
        }
        catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            problems.push({ kind: 'migration', id: entry.name, requiredBy: entry.id, hint: `${origin} 的 migrations 导出不可用：${detail}`, detail });
            continue;
        }
        const list = exported as readonly unknown[];
        for (const [index, raw] of list.entries()) {
            let parsed: ParsedMigration;
            try { parsed = parsePluginMigration(raw); }
            catch (error) {
                const detail = error instanceof Error ? error.message : String(error);
                problems.push({ kind: 'migration', id: entry.name, requiredBy: entry.id, hint: `${origin} 的 migrations[${index}] 形状不合法：${detail}`, detail });
                continue;
            }
            const key = migrationKey(parsed.owner, parsed.from, parsed.to);
            const existing = seen.get(key);
            if (existing) {
                if (canonical(existing.value) !== canonical(parsed.value))
                    problems.push({
                        kind: 'migration', id: parsed.owner, requiredBy: `${existing.id},${entry.id}`,
                        hint: `migration ${parsed.owner} (${parsed.from}->${parsed.to}) 在 ${existing.origin} 与 ${origin} 的内容不同`,
                        detail: `${existing.origin} vs ${origin}`
                    });
                continue;
            }
            seen.set(key, { value: parsed.value, origin, id: entry.id });
            result.push({ owner: parsed.owner, after: parsed.after, requiredBy: entry.name, value: parsed.value });
        }
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

function addActorIds(value: unknown, entry: PlannedEntry, references: Map<string, string[]>): void {
    if (typeof value === 'string') {
        if (!value)
            return;
        const owners = references.get(value) ?? [];
        if (!owners.includes(entry.id))
            owners.push(entry.id);
        references.set(value, owners);
        return;
    }
    if (Array.isArray(value)) {
        for (const item of value)
            addActorIds(item, entry, references);
        return;
    }
    if (value && typeof value === 'object') {
        const id = (value as { id?: unknown }).id;
        if (typeof id === 'string')
            addActorIds(id, entry, references);
    }
}

/** Actor ids referenced by active plugin config, grouped by the entry ids that reference them. */
function actorReferences(plan: CompositionPlan): Map<string, string[]> {
    const references = new Map<string, string[]>();
    for (const entry of active(plan)) {
        const config = entry.config as Record<string, unknown> | undefined;
        if (!config || typeof config !== 'object')
            continue;
        for (const key of ['actors', 'pairingGroups'])
            if (key in config)
                addActorIds(config[key], entry, references);
    }
    return references;
}

/** Initializer ids the profile needs at root creation; derived from the plugins that own actor creation and domain seeding. */
function requiredInitializers(plan: CompositionPlan): string[] {
    const entries = active(plan);
    const actors = entries.find(entry => entry.name === '@society0/core/actors');
    const ids: string[] = [];
    if (actors && (actors.config as { seed?: unknown }).seed === true)
        ids.push('actors.seed');
    if (entries.some(entry => entry.name === '@society0/core/plain-init'))
        ids.push('plain.seed');
    if (flag(plan, '@society0/social/social'))
        ids.push('social.seed');
    return ids;
}

interface ActorAnalysis {
    problems: ProfileProblem[];
    warnings: string[];
}

async function actorProblems(plan: CompositionPlan, world?: WorldDocument): Promise<ActorAnalysis> {
    const problems: ProfileProblem[] = [], warnings: string[] = [];
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
    if (seed) {
        const references = actorReferences(plan);
        if (references.size) {
            if (!world) {
                warnings.push(`profile 引用了主体 ${[...references.keys()].join(', ')}，但 check 没有读取 world；run start 必须提供包含这些主体的 --world 文件`);
            }
            else {
                const available = new Set((world.actors ?? []).map(actor => actor.id));
                for (const [id, owners] of references)
                    if (!available.has(id))
                        problems.push({
                            kind: 'actor', id, requiredBy: owners.join(','),
                            hint: `world 的 actors 段缺少主体 ${id}（${owners.join(', ')} 引用）；world 的 actors 段每条要有 id 和 source`
                        });
            }
        }
    }
    return { problems, warnings };
}

interface PairingOccurrence { count: number; owners: string[] }

/** pairingGroups 里每个主体出现了几次，用于把 UNIQUE social_pair_members.actor 翻成可修复的提示。 */
function pairingOccurrences(plan: CompositionPlan): Map<string, PairingOccurrence> {
    const occurrences = new Map<string, PairingOccurrence>();
    for (const entry of active(plan)) {
        const groups = (entry.config as { pairingGroups?: unknown } | undefined)?.pairingGroups;
        if (!Array.isArray(groups))
            continue;
        for (const group of groups) {
            if (!Array.isArray(group))
                continue;
            for (const actor of group) {
                if (typeof actor !== 'string' || !actor)
                    continue;
                const current = occurrences.get(actor) ?? { count: 0, owners: [] };
                current.count++;
                if (!current.owners.includes(entry.id))
                    current.owners.push(entry.id);
                occurrences.set(actor, current);
            }
        }
    }
    return occurrences;
}

function tableOwners(plan: CompositionPlan, table: string | undefined): string[] {
    if (!table)
        return [];
    const match = (prefix: string) => active(plan).filter(entry => entry.name.startsWith(prefix)).map(entry => entry.id);
    if (table === 's0_actors')
        return active(plan).filter(entry => entry.name === '@society0/core/actors').map(entry => entry.id);
    if (table.startsWith('social_'))
        return match('@society0/social/');
    if (table.startsWith('thread'))
        return active(plan).filter(entry => entry.name === '@society0/core/thread').map(entry => entry.id);
    if (table === 's0_runtime_events')
        return active(plan).filter(entry => entry.name === '@society0/core/runtime-events').map(entry => entry.id);
    return [];
}

function worldProblem(error: unknown, plan: CompositionPlan): ProfileProblem {
    const message = error instanceof Error ? error.message : String(error);
    const occurrences = pairingOccurrences(plan);
    if (/UNIQUE constraint failed: social_pair_members\.actor/.test(message)) {
        const duplicate = [...occurrences].find(([, value]) => value.count > 1);
        if (duplicate) {
            const [actor, value] = duplicate;
            return {
                kind: 'world', id: actor, requiredBy: value.owners.join(','),
                hint: `主体 ${actor} 出现在多组配对中，每个主体只能属于一组；检查 ${value.owners.join(', ')} 的 pairingGroups（或 world 里生成配对的条目），把 ${actor} 只保留在一组里`,
                detail: message
            };
        }
        return {
            kind: 'world', id: 'social_pair_members.actor',
            hint: `同一主体被写进多组配对（social_pair_members.actor 唯一）；检查 social-init 的 pairingGroups 与 world 的 actors 段，每个主体只能属于一组`,
            detail: message
        };
    }
    const sourceMissing = /ACTOR_SOURCE_MISSING(?:\s+(.+))?/.exec(message);
    if (sourceMissing) {
        const actor = sourceMissing[1]?.trim();
        return {
            kind: 'world', id: actor || 'ACTOR_SOURCE_MISSING',
            hint: actor
                ? `world 的 actors 段中主体 ${actor} 缺少 source；每条 actor 都要有 id 和 source`
                : 'world 的 actors 段缺少 source；每条 actor 都要有 id 和 source',
            detail: message
        };
    }
    const actorMissing = /ACTOR_MISSING(?:\s+(.+))?/.exec(message);
    if (actorMissing) {
        const actor = actorMissing[1]?.trim();
        const references = actor ? new Map([...occurrences].filter(([id]) => id === actor)) : new Map();
        const requiredBy = references.size
            ? [...references.values()].flatMap(value => value.owners).join(',')
            : active(plan).find(entry => entry.name === '@society0/core/actors')?.id;
        return {
            kind: 'world', id: actor || 'ACTOR_MISSING', requiredBy,
            hint: actor
                ? `主体 ${actor} 在根创建时不存在；在 world 的 actors 段加入该主体（每条要有 id 和 source），或改掉引用它的 profile 条目`
                : '根创建时引用的主体不存在；在 world 的 actors 段补齐这些主体，或改掉引用它们的 profile 条目',
            detail: message
        };
    }
    if (/FOREIGN KEY constraint failed/.test(message)) {
        return {
            kind: 'world', id: 'foreign-key',
            hint: `外键引用指向不存在的记录（${message}）；检查 world 的 actors 段是否包含被引用的主体，以及 profile initializer 写入的边、配对、消息等关联记录是否引用了 world 中不存在的主体`,
            detail: message
        };
    }
    const constraint = /(UNIQUE|NOT NULL|CHECK) constraint failed(?::?\s*([^\n]+))?/.exec(message);
    if (constraint) {
        const kind = constraint[1]!;
        const detail = constraint[2]?.trim();
        const tableMatch = detail ? /([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)/.exec(detail) : null;
        const table = tableMatch?.[1];
        const column = tableMatch?.[2];
        // CHECK failures may name only the expression (for example "kind IN (...)"),
        // so fall back to its leading field name to keep the hint actionable.
        const field = !tableMatch && detail ? /^([A-Za-z_][\w]*)\b/.exec(detail)?.[1] : undefined;
        const target = column ?? field;
        const owners = tableOwners(plan, table);
        const where = table
            ? `表 ${table}${column ? ` 的列 ${column}` : ''}`
            : field ? `字段 ${field}` : '根创建写入的数据';
        const scope = owners.length
            ? `profile 条目 ${owners.join(', ')}`
            : 'world 的 actors 段与写入该表的 profile 条目';
        const remedy = kind === 'NOT NULL'
            ? `检查 ${scope} 是否为${target ? `${column ? '列' : '字段'} ${target}` : '该字段'}写了空值`
            : kind === 'CHECK'
                ? `检查 ${scope} 写入${target ? `${column ? '列' : '字段'} ${target}` : '该记录'}的值是否满足约束`
                : `检查 ${scope}，保证每个主体 / 每条记录只写一次`;
        return {
            kind: 'world', id: table ? (column ? `${table}.${column}` : table) : field ? `root-creation.${field}` : 'root-creation',
            requiredBy: owners.join(',') || undefined,
            hint: `${where} 违反 ${kind} 约束${detail ? `（${detail}）` : ''}；${remedy}`,
            detail: message
        };
    }
    return {
        kind: 'world', id: 'root-creation',
        hint: `在临时数据库执行 run start 的根创建步骤时失败：${message}；检查 world 的 actors 段与 profile 的 initializer 配置`,
        detail: message
    };
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
    world?: string;
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
    const world = options.world ? await readWorld(options.world) : undefined;
    const host = await platformHost(modelSeeds(profile), options.base);
    try {
        const analysis = await host.ctx.profileLoader.analyze(profile as never, { baseURL: url.href, cliPatches: patches });
        const missing: ProfileProblem[] = [...analysis.missing];
        const missingSlots = new Set(slotProblems(analysis.plan).map(item => item.id));
        const installedSlots = await installedSlotProviders(profile, url, options.base, missingSlots);
        missing.push(...slotProblems(analysis.plan, installedSlots), ...credentialProblems(analysis.plan, Boolean(options.credentialsEnv)));
        const actors = await actorProblems(analysis.plan, world);
        missing.push(...actors.problems);
        const migrations = await collectMigrations(analysis.plan, missing);
        missing.push(...migrationProblems(migrations));
        const unique = new Map<string, ProfileProblem>();
        for (const item of missing)
            unique.set(`${item.kind}\u0000${item.id}\u0000${item.requiredBy ?? ''}`, item);
        if (world && unique.size === 0) {
            for (const item of await checkWorldRoot(options, analysis.plan))
                unique.set(`${item.kind}\u0000${item.id}\u0000${item.requiredBy ?? ''}`, item);
        }
        const plugins = [...new Set(active(analysis.plan).map(entry => entry.name))].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
        return {
            schema: CHECK_SCHEMA,
            ok: unique.size === 0,
            missing: [...unique.values()],
            warnings: [...analysis.warnings, ...actors.warnings],
            resolved: { plugins, slots: slotGraph(analysis.plan).provides },
            migrations: [...new Set(migrations.map(item => item.owner))].sort(compareCodePoints)
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
    const missing = [...analysis.missing, ...slotProblems(analysis.plan)];
    if (missing.length)
        throw new PlatformError('CHECK_FAILED', `profile 未通过装配检查：${missing.map(item => `${item.kind}:${item.id}`).join(', ')}`, '先运行 society0 check --profile <p> --json 查看缺失项');
    const entryOf = (name: string) => active(analysis.plan).find(entry => entry.name === name);
    const directory = resolve(options.dir);
    const lineage = resolve(options.lineage ?? join(dirname(directory), 'lineages', basename(directory)));
    const overrides: EntryPatch[] = [];
    const sqlite = entryOf('@society0/core/sqlite');
    if (sqlite) overrides.push({ id: sqlite.id, config: { ...(sqlite.config as Record<string, unknown>), directory, runId: basename(directory) } });
    const artifacts = entryOf('@society0/core/artifacts-local');
    if (artifacts) overrides.push({ id: artifacts.id, config: { ...(artifacts.config as Record<string, unknown>), directory: join(directory, 'artifacts') } });
    const history = entryOf('@society0/core/sqlite-history');
    if (history) overrides.push({ id: history.id, config: { ...(history.config as Record<string, unknown>), directory, runId: basename(directory) } });
    const attempts = entryOf('@society0/core/attempts');
    if (attempts) overrides.push({ id: attempts.id, config: { ...(attempts.config as Record<string, unknown>), directory: lineage, lineage: basename(lineage) } });
    overrides.push(...tickOverride(analysis.plan, options.ticks));
    const input = options.world && options.mode === 'start' ? await readWorld(options.world) : {};
    const host2 = await platformHost(modelSeeds(profile), options.base);
    try {
        const plan = await host2.ctx.profileLoader.compile(profile as never, { baseURL: url.href, cliPatches: [...cliPatches, ...overrides] });
        return { plan, profileUrl: url.href, profile, ticks: options.ticks, dir: directory, lineage, input };
    }
    finally {
        await host2.close();
    }
}

interface RunRoot {
    readonly host: Awaited<ReturnType<typeof createHost>>;
    readonly handle: RunHandle;
    readonly context: Context;
}

/** Boot the run host and publish the authoritative root: migrations, actors.seed and every initializer. */
async function createRunRoot(prepared: PreparedPlan, options: StartOptions): Promise<RunRoot> {
    const seed: BootEntry[] = HOST_BOOT.map(specifier => specifier === 'module-resolver' && options.base
        ? { specifier: '@society0/host/module-resolver', config: { baseURL: baseURLOf(options.base) } }
        : { specifier: '@society0/host/' + specifier });
    const modelEnabled = prepared.plan.flat.some(entry => !entry.disabled && entry.binding?.api.requires.some(required => required.service.key === 'modelCredentials' && !required.optional && required.phase === 'module'));
    if (modelEnabled || options.credentialsEnv)
        seed.push({ specifier: '@society0/host/slots' }, { specifier: '@society0/core/model-credentials', config: { stores: [] } });
    const host = await createHost({ baseURL: import.meta.url, boot: seed });
    try {
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
        const runId = basename(prepared.dir);
        const entryOf = (name: string) => active(plan).find(entry => entry.name === name);
        const migrationIssues: ProfileProblem[] = [];
        const migrations = await collectMigrations(plan, migrationIssues);
        migrationIssues.push(...migrationProblems(migrations));
        if (migrationIssues.length)
            throw new PlatformError(
                'MIGRATION_INVALID',
                `profile 的 migration 不可用：${migrationIssues.map(item => `${item.kind}:${item.id}`).join(', ')}`,
                migrationIssues.map(item => item.hint).join('；')
            );
        const migrationValues = migrations.map(item => item.value);
        const databaseEntry = entryOf('@society0/core/sqlite');
        const historyEntry = entryOf('@society0/core/sqlite-history');
        const event = flag(plan, '@society0/core/runtime-events');
        const runGroup = plan.flat.find(entry => entry.group && entry.scope === 'run');
        const bootstrapGroup = plan.flat.find(entry => entry.group && entry.scope === 'bootstrap');
        if (!runGroup || !bootstrapGroup)
            throw new PlatformError('MOUNT_GROUP_MISSING', 'profile 缺少 run 或 bootstrap 作用域组，无法挂载运行目录', '在 profile 里保留 run 组和 bootstrap 组');
        let context: Context | undefined;
        const handle = await host.ctx.runDirectory.create({
            runId, generation: plan, mount: { runEntryId: runGroup.id, bootstrapEntryId: bootstrapGroup.id },
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
                        operationId: 'create:' + runId, runId, directory: prepared.dir, input: prepared.input,
                        migrations: migrationValues as never,
                        initialFrontier: frontier,
                        initialControl: { generation: (databaseEntry!.config as { generation: string }).generation, controlSequence: (historyEntry!.config as { controlSequence: string }).controlSequence, planId: prepared.profileUrl },
                        requiredInitializerIds: requiredInitializers(plan)
                    });
                }
                const status = await ctx.history.status();
                return { root, metadata: ctx.database.metadata, database: ctx.database, state: status.state };
            }
        });
        return { host, handle, context: context! };
    }
    catch (error) {
        try { await host.close(); }
        catch { /* preserve original failure */ }
        throw error;
    }
}

/** Execute run start's root creation against a throwaway database under TMPDIR, then discard it. */
async function checkWorldRoot(options: {
    profile: string;
    lock?: string;
    patches?: string;
    world?: string;
    base?: string;
    credentialsEnv?: string;
}, plan: CompositionPlan): Promise<ProfileProblem[]> {
    if (!options.world)
        return [];
    const base = await mkdtemp(join(tmpdir(), 'society0-check-'));
    const directory = join(base, 'run');
    const lineage = join(base, 'lineage');
    try {
        await mkdir(directory, { recursive: true });
        await mkdir(lineage, { recursive: true });
        const start: StartOptions = {
            profile: options.profile, dir: directory, lineage,
            lock: options.lock, patches: options.patches, base: options.base,
            world: options.world, credentialsEnv: options.credentialsEnv,
            detach: false, mode: 'start'
        };
        let prepared: PreparedPlan;
        try { prepared = await prepareRunPlan(start); }
        catch (error) { return [worldProblem(error, plan)]; }
        let root: RunRoot;
        try { root = await createRunRoot(prepared, start); }
        catch (error) { return [worldProblem(error, prepared.plan)]; }
        try { await root.handle.close(); }
        finally { await root.host.close(); }
        return [];
    }
    finally {
        await rm(base, { recursive: true, force: true });
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

interface ActivationReport {
    readonly total: number;
    readonly completed: number;
    readonly failed: number;
    readonly byReason: Readonly<Record<string, number>>;
    /** 预算耗尽时先用完的额度计数：requests / toolCalls / turns。 */
    readonly limits: Readonly<Record<string, number>>;
    readonly failures: readonly {
        readonly actorId: string;
        readonly momentId?: string;
        readonly reason: string;
        readonly code?: string;
        readonly stage?: string;
        readonly slot?: string;
        readonly limit?: string;
    }[];
}

const MAX_ACTIVATION_FAILURES = 20;

/** Turn the run-controller activation summaries into the platform result summary. */
function activationReport(result: Record<string, unknown>): ActivationReport {
    const summary = result.activations && typeof result.activations === 'object' && !Array.isArray(result.activations)
        ? result.activations as { entries?: unknown }
        : undefined;
    const entries = Array.isArray(summary?.entries)
        ? summary.entries.filter(item => item && typeof item === 'object' && !Array.isArray(item)) as Record<string, unknown>[]
        : [];
    const byReason: Record<string, number> = {};
    const limits: Record<string, number> = {};
    const failures: ActivationReport['failures'][number][] = [];
    let completed = 0;
    for (const entry of entries) {
        const reason = typeof entry.reason === 'string' ? entry.reason : 'unknown';
        byReason[reason] = (byReason[reason] ?? 0) + 1;
        const limit = typeof entry.limit === 'string' ? entry.limit : undefined;
        if (limit)
            limits[limit] = (limits[limit] ?? 0) + 1;
        if (entry.requiredSatisfied === true) {
            completed++;
            continue;
        }
        if (failures.length >= MAX_ACTIVATION_FAILURES)
            continue;
        failures.push({
            actorId: typeof entry.actorId === 'string' ? entry.actorId : '',
            ...(typeof entry.momentId === 'string' ? { momentId: entry.momentId } : {}),
            reason,
            ...(typeof entry.code === 'string' ? { code: entry.code } : {}),
            ...(typeof entry.stage === 'string' ? { stage: entry.stage } : {}),
            ...(typeof entry.slot === 'string' ? { slot: entry.slot } : {}),
            ...(limit ? { limit } : {})
        });
    }
    return { total: entries.length, completed, failed: entries.length - completed, byReason, limits, failures };
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
        await readWorld(options.world);
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
    const isTerminal = (state: RunStatus['state']) => state === 'completed' || state === 'failed' || state === 'paused';
    let terminal = false;
    const writeStatus = async (state: RunStatus['state'], completedTicks: number, frontier: unknown, lastReason: string | null, pid?: number) => {
        if (terminal && !isTerminal(state))
            return;
        if (isTerminal(state))
            terminal = true;
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
    let stopping = false;
    const inflight = new Set<Promise<void>>();
    try {
        const prepared = await prepareRunPlan({ ...options, lineage });
        const root = await createRunRoot(prepared, { ...options, lineage });
        host = root.host;
        const { handle } = root;
        let context: Context = root.context;
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
        const ctx = context;
        interval = setInterval(() => {
            if (stopping)
                return;
            let task: Promise<void> | undefined;
            task = (async () => {
                try {
                    const status = await ctx.history.status();
                    if (stopping)
                        return;
                    const frontier = status.completePoint?.frontier;
                    if (frontier)
                        await writeStatus('running', Number(frontier.completedCount), frontier, null, process.pid);
                }
                catch { /* status is best-effort while running */ }
                finally { if (task) inflight.delete(task); }
            })();
            inflight.add(task);
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
        stopping = true;
        if (interval)
            clearInterval(interval);
        await Promise.all([...inflight]);
        const frontier = result.cut?.frontier ?? null;
        const completed = frontier ? Number(frontier.completedCount) : 0;
        await writeStatus(result.status, completed, frontier, result.reason ?? null, process.pid);
        await writeJsonAtomic(resultPath(directory), {
            schema: RESULT_SCHEMA, runId, dir: directory, state: result.status,
            result, activations: activationReport(result as unknown as Record<string, unknown>)
        });
        await consumer.dispose();
        await lease.release();
        await handle.close();
        await host.close();
        return result.status === 'completed' ? 0 : 1;
    }
    catch (error) {
        stopping = true;
        if (interval)
            clearInterval(interval);
        await Promise.all([...inflight]);
        const message = error instanceof Error ? error.message : String(error);
        try { await writeStatus('failed', 0, null, message); }
        catch { /* directory may not be writable */ }
        try { await host?.close(); }
        catch { /* preserve original failure */ }
        throw error;
    }
}
