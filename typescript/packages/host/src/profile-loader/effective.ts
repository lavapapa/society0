import { ValidationError } from '@society0/cordis';
import { assertLiteral, leaves, redact, type Json, type OriginStep, type ConfigOrigin } from '../config-resolvers/types.js';
import type { Resolution } from '../config-resolvers/index.js';
import { unknownConfigPaths } from '../config-resolvers/strict.js';
import type { PluginEntry, ModuleBinding, ConfigEvidence } from './types.js';
type Resolver = (value: Json) => Promise<Resolution>;
function version(binding: ModuleBinding | undefined): number { return binding?.metadata.configVersion ?? 1; }
async function resolveInput(entry: PluginEntry, resolve: Resolver | undefined): Promise<Resolution> {
    const value: Json = entry.group ? {} : structuredClone(entry.config ?? {});
    if (!entry.group && resolve)
        return resolve(value);
    return { value, evidence: [], secretPointers: [], dependencies: [] };
}
function resolutionStep(entry: PluginEntry, resolved: Resolution['evidence'][number], prior: readonly OriginStep[]): OriginStep {
    const source: OriginStep = prior.at(-1) ?? { layer: 'profile', sourceUri: entry.name, sourceVersion: '1', sourcePointer: resolved.pointer, operation: 'resolve' };
    return { ...source, operation: 'resolve', resolver: { name: resolved.name, version: resolved.version, operationId: `${entry.id}:${resolved.pointer}`, ...(resolved.secretVersion ? { secretVersion: resolved.secretVersion } : {}) } };
}
function recordResolutions(entry: PluginEntry, resolution: Resolution, before: ReadonlyMap<string, Json>, origins: Map<string, OriginStep[]>): void {
    for (const resolved of resolution.evidence) {
        const prior = origins.get(resolved.pointer) ?? [];
        const step = resolutionStep(entry, resolved, prior);
        for (const pointer of before.keys())
            if (pointer === resolved.pointer || pointer.startsWith(resolved.pointer + '/'))
                origins.set(pointer, [...prior, step]);
    }
}
function validateConfig(entry: PluginEntry, binding: ModuleBinding | undefined, config: Json): Json {
    const schema = binding?.plugin.Config;
    if (!schema)
        return config;
    const unknown = unknownConfigPaths(schema as never, config);
    if (unknown.length)
        throw Object.assign(Error(`UNKNOWN_CONFIG_FIELD ${entry.id}${unknown[0]}${unknown.length > 1 ? ` (+${unknown.length - 1})` : ''}`), { code: 'UNKNOWN_CONFIG_FIELD', entryId: entry.id, paths: unknown });
    const checked = schema['~standard'].validate(config);
    if ('then' in checked)
        throw Error(`ASYNC_SCHEMA ${entry.id}`);
    if (checked.issues)
        throw new ValidationError(checked.issues);
    assertLiteral(checked.value, entry.id);
    return checked.value;
}
function effectiveOrigins(entry: PluginEntry, binding: ModuleBinding | undefined, config: Json, before: ReadonlyMap<string, Json>, origins: ReadonlyMap<string, OriginStep[]>): ConfigOrigin[] {
    const result: ConfigOrigin[] = [];
    for (const pointer of leaves(config).keys()) {
        let steps = origins.get(pointer) ?? origins.get('') ?? [];
        if (!before.has(pointer))
            steps = [...steps, { layer: 'schema-default', sourceUri: entry.name, sourceVersion: String(version(binding)), sourcePointer: pointer, operation: 'default' }];
        result.push({ entryId: entry.id, effectivePointer: pointer, steps });
    }
    return result;
}
function policySecrets(binding: ModuleBinding | undefined): string[] {
    if (!binding)
        return [];
    return binding.metadata.configPolicy.filter(policy => policy.secret).map(policy => policy.path);
}
export async function effectiveConfig(entry: PluginEntry, binding: ModuleBinding | undefined, originMap: Map<string, OriginStep[]>, resolve: Resolver | undefined) {
    const resolution = await resolveInput(entry, resolve);
    const before = leaves(resolution.value);
    recordResolutions(entry, resolution, before, originMap);
    const config = validateConfig(entry, binding, resolution.value);
    const evidence: ConfigEvidence = { configId: entry.id, schemaVersion: version(binding), origins: effectiveOrigins(entry, binding, config, before, originMap), redactedEffective: redact(config, [...resolution.secretPointers, ...policySecrets(binding)]), secretVersions: Object.fromEntries(resolution.evidence.filter(item => item.secretVersion).map(item => [item.pointer, item.secretVersion!])) };
    return { config, evidence, dependencies: resolution.dependencies };
}
