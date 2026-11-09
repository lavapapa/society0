import { groupChildren } from './entries.js';
import { Inject } from '@society0/cordis';
import { freezeJson, type Json } from '../config-resolvers/types.js';
import type { Resolution } from '../config-resolvers/index.js';
import type { PluginScope } from '../module-resolver/index.js';
import type { ConfigLayer, ModuleBinding, PlannedEntry, CompositionPlan, PluginEntry, ProfileProblem } from './types.js';
import { composeLayers } from './layers.js';
import { groupServices, checkGraph, collectGraphProblems } from './graph.js';
import { effectiveConfig } from './effective.js';
export * from './types.js';
export { composeLayers } from './layers.js';
export interface CompileOptions {
    resolve?: (value: Json) => Promise<Resolution>;
    externalServices?: readonly string[];
    /** When provided, binding/config/graph faults are collected instead of thrown. */
    problems?: ProfileProblem[];
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
        if (!binding) {
            if (!this.options.problems)
                throw Error(`MODULE_BINDING_MISSING ${entry.id}/${entry.name}`);
            this.options.problems.push({ kind: 'plugin', id: entry.name, requiredBy: entry.id, hint: `插件 ${entry.name} 没有可用的模块绑定；检查 lockfile 与安装目录，或从 profile 中移除 ${entry.id}`, detail: 'MODULE_BINDING_MISSING' });
            return;
        }
        try {
            validateBinding(entry, binding, scope);
        }
        catch (error) {
            if (!this.options.problems)
                throw error;
            this.options.problems.push({ kind: 'plugin', id: entry.name, requiredBy: entry.id, hint: `插件 ${entry.id} 不能用于 ${scope} 作用域：${error instanceof Error ? error.message : String(error)}`, detail: 'INVALID_BINDING' });
            return;
        }
        return binding;
    }
    private async effective(entry: PluginEntry, binding: ModuleBinding | undefined) {
        try {
            return await effectiveConfig(entry, binding, this.origins.get(entry.id) ?? new Map(), this.options.resolve);
        }
        catch (error) {
            if (!this.options.problems)
                throw error;
            const message = error instanceof Error ? error.message : String(error);
            const paths = (error as { paths?: readonly string[] }).paths;
            this.options.problems.push({ kind: 'config', id: entry.id + (paths?.[0] ?? ''), requiredBy: entry.id, hint: `修正 ${entry.id} 的配置；${message}`, detail: message });
            const config = (entry.config ?? {}) as Json;
            return { config, evidence: { configId: entry.id, schemaVersion: 1, origins: [], redactedEffective: config, secretVersions: {} }, dependencies: [] as string[] };
        }
    }
    private async entry(entry: PluginEntry, parent: ParentScope): Promise<PlannedEntry> {
        const scope = entry.lifetime ?? parent.scope, disabled = parent.disabled || !!entry.disabled;
        const binding = this.binding(entry, scope), isolate = entryIsolation(entry, parent.isolate, this.bindings);
        const { config, evidence, dependencies } = await this.effective(entry, binding);
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
    if (options.problems) {
        for (const problem of collectGraphProblems(planner.flat, options.externalServices ?? []))
            options.problems.push({
                kind: 'service', id: problem.service ?? problem.entryId, requiredBy: problem.entryId,
                hint: problem.code === 'SERVICE_MISSING'
                    ? `补上提供 ${problem.service} 的插件，或在 profile 中移除 ${problem.entryId}`
                    : `调整 ${problem.entryId} 与 ${problem.providerId ?? problem.service} 的作用域/协议：${problem.code}`,
                detail: [problem.code, problem.providerId, problem.detail].filter(Boolean).join(' ')
            });
    }
    else
        checkGraph(planner.flat, options.externalServices ?? []);
    return Object.freeze({ entries: Object.freeze(entries), flat: Object.freeze(planner.flat), layers: Object.freeze(structuredClone(layers)) });
}
export function dumpPlan(plan: CompositionPlan) { const dump = (entry: PlannedEntry): unknown => ({ id: entry.id, name: entry.name, group: entry.group, scope: entry.scope, disabled: entry.disabled, config: entry.evidence.redactedEffective, inject: entry.inject, isolate: entry.isolate, origins: entry.evidence.origins, children: entry.children.map(dump) }); return { entries: plan.entries.map(dump) }; }
