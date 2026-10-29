import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from '../module-resolver/metadata.js';
import { Context, Service } from '@society0/cordis';
import { assertLiteral, pointerToken, type Json } from './types.js';
export const Config = Schema.object({});
export const metadata = hostMetadata('config-resolvers', 'Resolve literal references with non-value provenance');
export const api = hostApi('./config-resolvers', ["configResolvers"], []);
export * from './types.js';
export interface ConfigReference {
    readonly kind: string;
    readonly name: string;
    readonly parse?: 'string' | 'json';
    readonly projection?: string;
    readonly version?: string;
}
export interface ResolverResult {
    readonly value: Json;
    readonly version: string;
    readonly secretVersion?: string;
    readonly dependency?: string;
}
export interface ConfigResolver {
    readonly name: string;
    readonly deterministic: boolean;
    resolve(ref: ConfigReference, ctx: Context): ResolverResult | Promise<ResolverResult>;
}
export interface Resolution {
    readonly value: Json;
    readonly evidence: readonly {
        readonly pointer: string;
        readonly name: string;
        readonly version: string;
        readonly secretVersion?: string;
    }[];
    readonly secretPointers: readonly string[];
    readonly dependencies: readonly string[];
}
function jsonObject(value: Json | undefined): value is Readonly<Record<string, Json>> { return !!value && typeof value === 'object' && !Array.isArray(value); }
function reference(value: Readonly<Record<string, Json>>, pointer: string): ConfigReference {
    if (Object.keys(value).length !== 1)
        throw Error(`REF_FIELDS ${pointer}`);
    const raw = value.$ref;
    if (!jsonObject(raw) || typeof raw.kind !== 'string' || typeof raw.name !== 'string')
        throw Error(`REF_INVALID ${pointer}`);
    return raw as unknown as ConfigReference;
}
class ResolutionWalk {
    private readonly evidence: Resolution['evidence'][number][] = [];
    private readonly secretPointers: string[] = [];
    private readonly dependencies = new Set<string>();
    constructor(private resolvers: ReadonlyMap<string, ConfigResolver>, private ctx: Context) { }
    private record(pointer: string, ref: ConfigReference, resolver: ConfigResolver, result: ResolverResult): void {
        this.evidence.push({ pointer, name: resolver.name, version: result.version, ...(result.secretVersion ? { secretVersion: result.secretVersion } : {}) });
        if (result.secretVersion || ref.kind === 'secret')
            this.secretPointers.push(pointer);
        if (result.dependency)
            this.dependencies.add(result.dependency);
    }
    private async resolveReference(ref: ConfigReference, pointer: string): Promise<Json> {
        const resolver = this.resolvers.get(ref.kind);
        if (!resolver)
            throw Error(`RESOLVER_PENDING ${ref.kind} at ${pointer}`);
        const result = await resolver.resolve(ref, this.ctx);
        assertLiteral(result.value, pointer);
        this.record(pointer, ref, resolver, result);
        return structuredClone(result.value);
    }
    private async visit(value: Json, pointer: string): Promise<Json> {
        if (jsonObject(value) && Object.hasOwn(value, '$ref'))
            return this.resolveReference(reference(value, pointer), pointer);
        if (Array.isArray(value))
            return Promise.all(value.map((child, i) => this.visit(child, `${pointer}/${i}`)));
        if (jsonObject(value))
            return Object.fromEntries(await Promise.all(Object.entries(value).map(async ([key, child]) => [key, await this.visit(child, `${pointer}/${pointerToken(key)}`)])));
        return value;
    }
    async run(value: Json): Promise<Resolution> { return { value: await this.visit(value, ''), evidence: this.evidence, secretPointers: this.secretPointers, dependencies: [...this.dependencies] }; }
}
declare module '@society0/cordis' {
    interface Context {
        configResolvers: ConfigResolverRegistry;
    }
}
export class ConfigResolverRegistry extends Service {
    static Config = Config;
    private readonly resolvers = new Map<string, ConfigResolver>();
    constructor(ctx: Context) { super(ctx, 'configResolvers'); }
    register(resolver: ConfigResolver): () => void | Promise<void> {
        return this.ctx.effect(() => {
            if (this.resolvers.has(resolver.name))
                throw Error(`RESOLVER_DUPLICATE ${resolver.name}`);
            this.resolvers.set(resolver.name, resolver);
            return () => {
                if (this.resolvers.get(resolver.name) === resolver)
                    this.resolvers.delete(resolver.name);
            };
        }, `configResolver(${resolver.name})`);
    }
    async resolve(value: Json): Promise<Resolution> { assertLiteral(value); return new ResolutionWalk(this.resolvers, this.ctx).run(value); }
}
export function envResolver(snapshot: Readonly<Record<string, string | undefined>>): ConfigResolver {
    const environment = Object.freeze({ ...snapshot });
    return { name: 'env', deterministic: true, resolve(ref) {
            const raw = environment[ref.name];
            if (raw === undefined)
                throw Error(`ENV_MISSING ${ref.name}`);
            const value: unknown = ref.parse === 'json' ? JSON.parse(raw) : raw;
            assertLiteral(value);
            return { value, version: 'snapshot' };
        } };
}
export function secretResolver(read: (name: string) => Promise<{
    value: Json;
    version: string;
}>): ConfigResolver { return { name: 'secret', deterministic: false, async resolve(ref) { const result = await read(ref.name); return { value: result.value, version: result.version, secretVersion: result.version }; } }; }
export function serviceResolver(): ConfigResolver {
    return { name: 'service', deterministic: false, async resolve(ref, ctx) {
            const provider: unknown = ctx.get(ref.name);
            if (!provider || typeof provider !== 'object' || !('project' in provider) || typeof provider.project !== 'function')
                throw Error(`PROJECTION_PENDING ${ref.name}`);
            const result: ResolverResult = await provider.project(ref.projection, ref.version);
            if (ref.version && ref.version !== result.version)
                throw Error(`PROJECTION_VERSION ${ref.name}`);
            return { ...result, dependency: ref.name };
        } };
}
export default ConfigResolverRegistry;
