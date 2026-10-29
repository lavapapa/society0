import type { PreflightSession } from '../packages.js';
import type { Context } from '@society0/cordis';
import { readFile } from 'node:fs/promises';
import { load as yamlLoad } from 'js-yaml';
import { entryListSchema } from '@society0/include';
import { assertLiteral, type Json } from '../config-resolvers/types.js';
import type { ModuleLock } from '../module-resolver/index.js';
import { groupChildren } from './entries.js';
import type { EntryPatch, ConfigLayer, PluginEntry, ModuleBinding } from './types.js';
export interface Profile {
    readonly format: 'society-profile/v2';
    readonly bundles: readonly {
        package: string;
        version: string;
    }[];
    readonly patches: readonly EntryPatch[];
    readonly configSources: readonly string[];
    readonly lockfile: string;
}
export async function readLiteral(url: string): Promise<Json> { const text = await readFile(new URL(url), 'utf8'); const value: unknown = url.endsWith('.json') ? JSON.parse(text) : yamlLoad(text, { schema: entryListSchema }); assertLiteral(value, url); return value; }
function allEntries(entries: readonly PluginEntry[]): PluginEntry[] { return entries.flatMap(entry => [entry, ...(entry.group ? allEntries(groupChildren(entry)) : [])]); }
export function validateProfile(profile: Profile): void {
    assertLiteral(profile);
    for (const key of Object.keys(profile))
        if (!['format', 'bundles', 'patches', 'configSources', 'lockfile'].includes(key))
            throw Error(`PROFILE_FIELD ${key}`);
    if (profile.format !== 'society-profile/v2' || !Array.isArray(profile.bundles) || !Array.isArray(profile.patches) || !Array.isArray(profile.configSources) || typeof profile.lockfile !== 'string')
        throw Error('PROFILE_FORMAT');
}
async function patchesAt(uri: string, kind: 'BUNDLE' | 'MACHINE'): Promise<readonly EntryPatch[]> {
    const patches = await readLiteral(uri);
    if (!Array.isArray(patches))
        throw Error(`${kind}_PATCH_ARRAY ${uri}`);
    return patches as unknown as EntryPatch[];
}
export async function loadLayers(ctx: Context, profile: Profile, base: string, lock: ModuleLock, cliPatches: readonly EntryPatch[] | undefined): Promise<ConfigLayer[]> {
    const layers: ConfigLayer[] = [];
    for (const bundle of profile.bundles) {
        const identity = ctx.moduleResolver.bundle(bundle.package, bundle.version, lock);
        layers.push({ kind: 'bundle', uri: identity.url, version: identity.version, patches: await patchesAt(identity.url, 'BUNDLE') });
    }
    layers.push({ kind: 'profile', uri: base, version: '1', patches: profile.patches });
    for (const source of profile.configSources) {
        const uri = new URL(source, base).href;
        layers.push({ kind: 'machine', uri, version: '1', patches: await patchesAt(uri, 'MACHINE') });
    }
    if (cliPatches)
        layers.push({ kind: 'cli', uri: base + '#argv', version: '1', patches: cliPatches });
    return layers;
}
async function bindingFor(ctx: Context, entry: PluginEntry, lock: ModuleLock, session: PreflightSession): Promise<ModuleBinding> {
    const identity = ctx.moduleResolver.resolve(entry.name, lock, session), inspection = ctx.moduleResolver.inspect(identity);
    const meta = inspection.metadata?.entries.find(value => value.exportPath === identity.exportPath), api = inspection.api?.entries.find(value => value.exportPath === identity.exportPath);
    if (!meta || !api)
        throw Error(`STATIC_METADATA_REQUIRED ${entry.name}`);
    const module = await ctx.moduleResolver.importVerified(identity);
    return { plugin: module.plugin, metadata: meta.metadata, api, identity };
}
export async function loadBindings(ctx: Context, tree: readonly PluginEntry[], lock: ModuleLock, session: PreflightSession): Promise<Record<string, ModuleBinding>> {
    const bindings: Record<string, ModuleBinding> = {};
    for (const entry of allEntries(tree)) {
        if (entry.group || bindings[entry.name])
            continue;
        bindings[entry.name] = await bindingFor(ctx, entry, lock, session);
    }
    return bindings;
}
export function externalServices(ctx: Context, tree: readonly PluginEntry[], bindings: Readonly<Record<string, ModuleBinding>>): string[] {
    const needed = new Set(Object.values(bindings).flatMap(binding => binding.api.requires.map(required => required.service.key)));
    for (const entry of allEntries(tree))
        for (const key of entry.inject ?? [])
            needed.add(key);
    return [...needed].filter(key => ctx.get(key) !== undefined);
}
