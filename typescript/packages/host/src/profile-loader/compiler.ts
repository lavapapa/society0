import { groupChildren } from './entries.js';
import { Inject } from '@society0/cordis';
import { freezeJson, type Json } from '../config-resolvers/types.js';
import type { Resolution } from '../config-resolvers/index.js';
import type { PluginScope } from '../module-resolver/index.js';
import type { ConfigLayer, ModuleBinding, PlannedEntry, CompositionPlan, PluginEntry } from './types.js';
import { composeLayers } from './layers.js';
import { groupServices, checkGraph } from './graph.js';
import { effectiveConfig } from './effective.js';
export * from './types.js';
export { composeLayers } from './layers.js';
export interface CompileOptions {
    resolve?: (value: Json) => Promise<Resolution>;
    externalServices?: readonly string[];
}
interface ParentScope {
    scope: PluginScope;
    parentId: string | undefined;
    isolate: Record<string, string>;
    disabled: boolean;
}
function validateBinding(entry: PluginEntry, binding: ModuleBinding, scope: PluginScope): void {
    if (!(binding.metadata.allowedScopes ?? [binding.metadata.scope]).includes(scope))
        throw Error(`PLUGIN_SCOPE ${entry.id}: ${scope}`);
    for (const policy of binding.metadata.configPolicy)
        if (policy.mode === 'live' && policy.affectsResults)
            throw Error(`FIELD_POLICY ${entry.id}${policy.path}: live affects results`);
}
function isolateLabel(entry: PluginEntry, key: string, label: unknown): string {
    if (typeof label !== 'boolean' && typeof label !== 'string')
        throw Error(`ISOLATE_VALUE ${entry.id}/${key}`);
    if (label === false)
        return `root:${key}`;
    if (label === true)
        return `${entry.id}:${key}`;
    return label;
}
function entryIsolation(entry: PluginEntry, inherited: Record<string, string>, bindings: Readonly<Record<string, ModuleBinding>>): Record<string, string> {
    const isolate = { ...inherited };
    if (entry.group && entry.lifetime && entry.lifetime !== 'host')
        for (const key of groupServices(groupChildren(entry), bindings))
            isolate[key] = `${entry.id}:${key}`;
    for (const [key, label] of Object.entries(entry.isolate ?? {}))
        isolate[key] = isolateLabel(entry, key, label);
    return isolate;
}
function moduleRequirements(binding: ModuleBinding | undefined): string[] {
    if (!binding)
        return [];
    return binding.api.requires.filter(required => !required.optional && required.phase === 'module').map(required => required.service.key);
}
function entryInjections(entry: PluginEntry, binding: ModuleBinding | undefined, dependencies: readonly string[]): string[] { return [...new Set([...(entry.inject ?? []), ...Object.keys(Inject.resolve(binding?.plugin.inject)), ...moduleRequirements(binding), ...dependencies])]; }
class EntryPlanner {
    readonly flat: PlannedEntry[] = [];
    constructor(private bindings: Readonly<Record<string, ModuleBinding>>, private origins: ReturnType<typeof composeLayers>['origins'], private options: CompileOptions) { }
    private binding(entry: PluginEntry, scope: PluginScope): ModuleBinding | undefined {
        if (entry.group)
            return;
        const binding = this.bindings[entry.name];
        if (!binding)
            throw Error(`MODULE_BINDING_MISSING ${entry.id}/${entry.name}`);
        validateBinding(entry, binding, scope);
        return binding;
    }
    private async entry(entry: PluginEntry, parent: ParentScope): Promise<PlannedEntry> {
        const scope = entry.lifetime ?? parent.scope, disabled = parent.disabled || !!entry.disabled;
        const binding = this.binding(entry, scope), isolate = entryIsolation(entry, parent.isolate, this.bindings);
        const { config, evidence, dependencies } = await effectiveConfig(entry, binding, this.origins.get(entry.id) ?? new Map(), this.options.resolve);
        const inject = entryInjections(entry, binding, dependencies);
        const children = entry.group ? await this.build(groupChildren(entry), { scope, parentId: entry.id, isolate, disabled }) : [];
        const planned: PlannedEntry = Object.freeze({ id: entry.id, name: entry.name, group: !!entry.group, scope, parentId: parent.parentId, disabled, config: freezeJson(config), inject: Object.freeze(inject), isolate: Object.freeze(isolate), intercept: Object.freeze({ ...entry.intercept }), children: Object.freeze(children), binding, evidence });
        this.flat.push(planned);
        return planned;
    }
    async build(entries: readonly PluginEntry[], parent: ParentScope): Promise<PlannedEntry[]> {
        const result: PlannedEntry[] = [];
        for (const entry of entries)
            result.push(await this.entry(entry, parent));
        return result;
    }
}
export async function compileLayers(layers: readonly ConfigLayer[], bindings: Readonly<Record<string, ModuleBinding>>, options: CompileOptions = {}): Promise<CompositionPlan> {
    const { tree, origins } = composeLayers(layers);
    const planner = new EntryPlanner(bindings, origins, options);
    const entries = await planner.build(tree, { scope: 'host', parentId: undefined, isolate: {}, disabled: false });
    checkGraph(planner.flat, options.externalServices ?? []);
    return Object.freeze({ entries: Object.freeze(entries), flat: Object.freeze(planner.flat), layers: Object.freeze(structuredClone(layers)) });
}
export function dumpPlan(plan: CompositionPlan) { const dump = (entry: PlannedEntry): unknown => ({ id: entry.id, name: entry.name, group: entry.group, scope: entry.scope, disabled: entry.disabled, config: entry.evidence.redactedEffective, inject: entry.inject, isolate: entry.isolate, origins: entry.evidence.origins, children: entry.children.map(dump) }); return { entries: plan.entries.map(dump) }; }
