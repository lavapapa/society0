import { groupChildren } from './entries.js';
import { applyEntryPatches } from '@society0/include';
import { assertLiteral, type Json, type OriginStep } from '../config-resolvers/types.js';
import type { PluginScope } from '../module-resolver/index.js';
import type { PluginEntry, EntryPatch, ConfigLayer } from './types.js';
const entryFields = new Set(['id', 'name', 'config', 'group', 'lifetime', 'disabled', 'inject', 'isolate', 'intercept']);
const patchFields = new Set([...entryFields].filter(k => k !== 'group').concat('insert'));
const lifetimes: Record<PluginScope, readonly PluginScope[]> = { host: ['host', 'run'], run: ['bootstrap', 'generation'], generation: ['activation'], bootstrap: [], activation: [] };
function fields(value: object, allowed: Set<string>, where: string) {
    for (const key of Object.keys(value))
        if (!allowed.has(key))
            throw Error(`UNKNOWN_FIELD ${where}/${key}`);
}
function entriesMap(entries: readonly PluginEntry[], map = new Map<string, PluginEntry>()) {
    for (const entry of entries) {
        if (map.has(entry.id))
            throw Error(`DUPLICATE_ID ${entry.id}`);
        map.set(entry.id, entry);
        if (entry.group)
            entriesMap(groupChildren(entry), map);
    }
    return map;
}
function identity(entry: PluginEntry, parent: string): void {
    if (typeof entry.id !== 'string' || !entry.id || typeof entry.name !== 'string')
        throw Error(`ENTRY_IDENTITY ${parent}`);
}
function flags(entry: PluginEntry): void {
    for (const key of ['group', 'disabled'] as const)
        if (entry[key] !== undefined && typeof entry[key] !== 'boolean')
            throw Error(`ENTRY_BOOLEAN ${entry.id}/${key}`);
}
function injections(entry: PluginEntry): void {
    if (entry.inject !== undefined && (!Array.isArray(entry.inject) || entry.inject.some(value => typeof value !== 'string')))
        throw Error(`ENTRY_INJECT ${entry.id}`);
}
function lifetime(entry: PluginEntry, scope: PluginScope, parent: string): void {
    if (entry.lifetime && !entry.group)
        throw Error(`LIFETIME_REQUIRES_GROUP ${entry.id}`);
    if (entry.lifetime && !lifetimes[scope].includes(entry.lifetime))
        throw Error(`LIFETIME_EDGE ${parent}:${scope} -> ${entry.id}:${entry.lifetime}`);
}
function validateEntries(entries: readonly PluginEntry[], scope: PluginScope, parent: string): void {
    for (const entry of entries) {
        fields(entry, entryFields, entry.id);
        identity(entry, parent);
        flags(entry);
        injections(entry);
        lifetime(entry, scope, parent);
        if (entry.group) {
            if (entry.config !== undefined && !Array.isArray(entry.config))
                throw Error(`GROUP_CHILDREN ${entry.id}`);
            validateEntries(groupChildren(entry), entry.lifetime ?? scope, entry.id);
        }
    }
}
function validate(entries: readonly PluginEntry[]): void { entriesMap(entries); validateEntries(entries, 'host', 'root'); }
function configPointers(value: Json, path = ''): string[] { return [path, ...(value && typeof value === 'object' ? Object.entries(value).flatMap(([key, v]) => configPointers(v, `${path}/${key.replaceAll('~', '~0').replaceAll('/', '~1')}`)) : [])]; }
function location(entries: readonly PluginEntry[], id: string, path: string): string | undefined {
    for (const [i, entry] of entries.entries()) {
        if (entry.id === id)
            return `${path}/${i}/config`;
        if (entry.group) {
            const found = location(groupChildren(entry), id, `${path}/${i}/config`);
            if (found)
                return found;
        }
    }
}
function configChanged(entry: PluginEntry, before: ReadonlyMap<string, PluginEntry>, patch: EntryPatch): boolean {
    if (entry.group)
        return false;
    return !before.has(entry.id) || (patch.id === entry.id && Object.hasOwn(patch, 'config'));
}
function patchSource(patch: EntryPatch, id: string, index: number): string | undefined { return patch.insert ? location(patch.insert, id, `/patches/${index}/insert`) : `/patches/${index}/config`; }
class LayerComposition {
    private tree: PluginEntry[] = [];
    private origins = new Map<string, Map<string, OriginStep[]>>();
    private record(entry: PluginEntry, before: ReadonlyMap<string, PluginEntry>, patch: EntryPatch, layer: ConfigLayer, index: number): void {
        if (!configChanged(entry, before, patch))
            return;
        const prior = this.origins.get(entry.id) ?? new Map();
        const next = new Map<string, OriginStep[]>();
        const source = patchSource(patch, entry.id, index);
        const operation = before.has(entry.id) ? 'replace' : 'insert';
        for (const pointer of configPointers(entry.config ?? {}))
            next.set(pointer, [...(prior.get(pointer) ?? []), { layer: layer.kind, sourceUri: layer.uri, sourceVersion: layer.version, sourcePointer: `${source}${pointer}`, operation }]);
        this.origins.set(entry.id, next);
    }
    apply(layer: ConfigLayer, patch: EntryPatch, index: number): void {
        fields(patch, patchFields, `${layer.uri}/patches/${index}`);
        const before = entriesMap(this.tree);
        this.tree = applyEntryPatches(this.tree, [structuredClone(patch)], (message, ...args) => { throw Error(`PATCH_ERROR ${layer.uri}/patches/${index}: ${message} ${args.join(' ')}`); });
        validate(this.tree);
        for (const entry of entriesMap(this.tree).values())
            this.record(entry, before, patch, layer, index);
    }
    result() { return { tree: this.tree, origins: this.origins }; }
}
export function composeLayers(layers: readonly ConfigLayer[]) {
    const composition = new LayerComposition();
    for (const layer of layers) {
        assertLiteral(layer.patches);
        for (const [index, patch] of layer.patches.entries())
            composition.apply(layer, patch, index);
    }
    return composition.result();
}
