import type { Plugin } from '@society0/cordis';
import type { Json, ConfigOrigin } from '../config-resolvers/types.js';
import type { PluginMetadata, ApiEntry, ModuleIdentity, PluginScope } from '../module-resolver/index.js';
export interface PluginEntry {
    id: string;
    name: string;
    config?: Json;
    group?: boolean;
    lifetime?: PluginScope;
    disabled?: boolean;
    inject?: string[];
    isolate?: Record<string, boolean | string>;
    intercept?: Record<string, Json>;
}
export interface EntryPatch {
    id?: string;
    name?: string;
    insert?: PluginEntry[];
    config?: Json;
    lifetime?: PluginScope;
    disabled?: boolean;
    inject?: string[];
    isolate?: Record<string, boolean | string>;
    intercept?: Record<string, Json>;
}
export interface ConfigLayer {
    readonly kind: 'bundle' | 'profile' | 'machine' | 'cli';
    readonly uri: string;
    readonly version: string;
    readonly patches: readonly EntryPatch[];
}
export interface ModuleBinding {
    readonly plugin: Plugin;
    readonly metadata: PluginMetadata;
    readonly api: ApiEntry;
    readonly identity?: ModuleIdentity;
}
export interface ConfigEvidence {
    readonly configId: string;
    readonly schemaVersion: number;
    readonly origins: readonly ConfigOrigin[];
    readonly redactedEffective: Json;
    readonly secretVersions: Readonly<Record<string, string>>;
}
export interface PlannedEntry {
    readonly id: string;
    readonly name: string;
    readonly group: boolean;
    readonly scope: PluginScope;
    readonly parentId?: string;
    readonly disabled: boolean;
    readonly config: Json;
    readonly inject: readonly string[];
    readonly isolate: Readonly<Record<string, string>>;
    readonly intercept: Readonly<Record<string, Json>>;
    readonly children: readonly PlannedEntry[];
    readonly binding?: ModuleBinding;
    readonly evidence: ConfigEvidence;
}
export interface CompositionPlan {
    readonly entries: readonly PlannedEntry[];
    readonly flat: readonly PlannedEntry[];
    readonly layers: readonly ConfigLayer[];
}
/** Stable platform-facing diagnostic item produced by ProfileLoader.analyze. */
export interface ProfileProblem {
    readonly kind: 'slot' | 'service' | 'migration' | 'plugin' | 'config' | 'credential';
    readonly id: string;
    readonly requiredBy?: string;
    readonly hint: string;
    readonly detail?: string;
}
export interface ProfileAnalysis {
    readonly plan: CompositionPlan;
    readonly missing: readonly ProfileProblem[];
    readonly warnings: readonly string[];
}
