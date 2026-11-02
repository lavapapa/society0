import type { Plugin } from '@society0/cordis';
import type { Json, ProgramSource } from '../config-resolvers/types.js';
export type PluginScope = 'host' | 'run' | 'bootstrap' | 'generation' | 'activation';
export interface PluginMetadata {
    readonly title: string;
    readonly summary: string;
    readonly configVersion: number;
    readonly configPolicy: readonly {
        path: string;
        mode: 'live' | 'boundary' | 'recreate' | 'new-run';
        affectsResults: boolean;
        secret?: boolean;
    }[];
    readonly scope: PluginScope;
    readonly allowedScopes?: readonly PluginScope[];
    readonly hostAbi: string;
}
export interface ServiceDescriptor {
    readonly key: string;
    readonly protocol: string;
    readonly major: number;
    readonly capabilities: readonly string[];
}
export interface ApiEntry {
    readonly exportPath: string;
    readonly provides: readonly ServiceDescriptor[];
    readonly requires: readonly {
        service: ServiceDescriptor;
        optional: boolean;
        phase: 'module' | 'dynamic';
    }[];
    readonly slots?: readonly Json[];
}
export interface MetadataSidecar {
    readonly format: 'society-plugin-metadata/v1';
    readonly package: {
        name: string;
        version: string;
    };
    readonly source: ProgramSource;
    readonly entries: readonly {
        exportPath: string;
        metadata: PluginMetadata;
        configSchemaPointer: string;
        apiPointer: string;
    }[];
}
export interface StaticInspection {
    readonly mode: 'manifest-only';
    readonly identity: ModuleIdentity;
    readonly metadata?: MetadataSidecar;
    readonly configSchema?: {
        format: 'society-config-schema/v1';
        schema: Json;
        limitations: readonly Json[];
    };
    readonly api?: {
        format: 'society-plugin-api/v1';
        entries: readonly ApiEntry[];
    };
    readonly limitations: readonly string[];
}
export interface ModuleLock {
    readonly lockfile: string;
    readonly packages: Readonly<Record<string, {
        readonly version: string;
        readonly gitCommit: string;
        readonly dirty: boolean;
        readonly patches: readonly string[];
        readonly hostAbi: string;
    }>>;
}
export interface ModuleIdentity {
    readonly specifier: string;
    readonly package: string;
    readonly version: string;
    readonly exportPath: string;
    readonly url: string;
    readonly directory: string;
    readonly source: ProgramSource;
}
export interface PackageManifest {
    name: string;
    version: string;
    exports?: Record<string, unknown> | string;
    society?: {
        plugin?: {
            metadata: string;
            configSchema: string;
            api: string;
        };
        bundle?: {
            patch: string;
        };
    };
}
export interface VerifiedModule {
    readonly identity: ModuleIdentity;
    readonly namespace: Readonly<Record<string, unknown>>;
    readonly plugin: Plugin;
}
