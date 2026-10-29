import { groupChildren } from './entries.js';
import type { PluginScope, ApiEntry } from '../module-resolver/index.js';
import type { PluginEntry, ModuleBinding, PlannedEntry } from './types.js';
export function groupServices(entries: readonly PluginEntry[], bindings: Readonly<Record<string, ModuleBinding>>): string[] { return entries.flatMap(entry => entry.group ? (entry.lifetime ? [] : groupServices(groupChildren(entry), bindings)) : (bindings[entry.name]?.api.provides ?? []).map(s => s.key)); }
type Edge = {
    id: string;
    why: string;
};
type Graph = Map<string, Edge[]>;
const allowed: Record<PluginScope, readonly PluginScope[]> = { host: ['host'], run: ['host', 'run'], bootstrap: ['host', 'run', 'bootstrap'], generation: ['host', 'run', 'generation'], activation: ['host', 'run', 'generation', 'activation'] };
function realm(entry: PlannedEntry, key: string): string { return entry.isolate[key] ?? `root:${key}`; }
function provisions(entry: PlannedEntry) { return entry.binding?.api.provides ?? []; }
function providerIndex(active: readonly PlannedEntry[]): Map<string, PlannedEntry[]> {
    const result = new Map<string, PlannedEntry[]>();
    for (const entry of active)
        for (const provided of provisions(entry)) {
            const key = realm(entry, provided.key);
            const values = result.get(key) ?? [];
            values.push(entry);
            result.set(key, values);
        }
    return result;
}
function missing(entry: PlannedEntry, key: string, active: readonly PlannedEntry[]): never {
    const hidden = active.find(candidate => provisions(candidate).some(service => service.key === key));
    if (hidden)
        throw Error(`LIFETIME_INVERSION ${entry.id} requires ${key} from ${hidden.id}:${hidden.scope}`);
    throw Error(`SERVICE_MISSING ${entry.id} requires ${key}`);
}
function providerFor(entry: PlannedEntry, key: string, providers: ReadonlyMap<string, readonly PlannedEntry[]>, active: readonly PlannedEntry[], external: readonly string[]): PlannedEntry | undefined {
    const matches = providers.get(realm(entry, key)) ?? [];
    if (matches.length > 1)
        throw Error(`SERVICE_AMBIGUOUS ${entry.id}/${key}: ${matches.map(value => value.id).join(', ')}`);
    if (matches.length === 1)
        return matches[0];
    if (external.includes(key) && realm(entry, key) === `root:${key}`)
        return;
    return missing(entry, key, active);
}
function compatible(need: ApiEntry['provides'][number], offered: ApiEntry['provides'][number]): boolean { return need.protocol === offered.protocol && need.major === offered.major && need.capabilities.every(capability => offered.capabilities.includes(capability)); }
function checkProtocol(entry: PlannedEntry, provider: PlannedEntry, key: string): void {
    const need = entry.binding?.api.requires.find(required => required.service.key === key)?.service;
    const offered = provisions(provider).find(service => service.key === key);
    if (need && offered && !compatible(need, offered))
        throw Error(`SERVICE_PROTOCOL ${entry.id}/${key} -> ${provider.id}`);
}
function checkLifetime(entry: PlannedEntry, provider: PlannedEntry, key: string): void {
    if (!allowed[entry.scope].includes(provider.scope))
        throw Error(`LIFETIME_INVERSION ${entry.id} requires ${key} from ${provider.id}:${provider.scope}`);
}
function addEdges(active: readonly PlannedEntry[], providers: ReadonlyMap<string, readonly PlannedEntry[]>, external: readonly string[], graph: Graph): void {
    for (const entry of active) {
        if (entry.parentId && graph.has(entry.parentId))
            graph.get(entry.parentId)!.push({ id: entry.id, why: 'child' });
        for (const key of entry.inject) {
            const provider = providerFor(entry, key, providers, active, external);
            if (!provider)
                continue;
            checkLifetime(entry, provider, key);
            checkProtocol(entry, provider, key);
            graph.get(provider.id)!.push({ id: entry.id, why: key });
        }
    }
}
class DependencyWalk {
    private readonly done = new Set<string>();
    private readonly path: string[] = [];
    constructor(private graph: Graph) { }
    visit(id: string): void {
        if (this.path.includes(id))
            throw Error(`DEPENDENCY_CYCLE ${[...this.path.slice(this.path.indexOf(id)), id].join(' -> ')}`);
        if (this.done.has(id))
            return;
        this.path.push(id);
        for (const edge of this.graph.get(id) ?? [])
            this.visit(edge.id);
        this.path.pop();
        this.done.add(id);
    }
}
export function checkGraph(flat: readonly PlannedEntry[], external: readonly string[]) {
    const active = flat.filter(entry => !entry.disabled), providers = providerIndex(active);
    const graph: Graph = new Map(active.map(entry => [entry.id, []]));
    addEdges(active, providers, external, graph);
    const walk = new DependencyWalk(graph);
    for (const id of graph.keys())
        walk.visit(id);
}
