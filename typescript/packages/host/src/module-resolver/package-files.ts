import { createRequire } from 'node:module';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { join, resolve, relative } from 'node:path';
import { exports as resolveExports } from 'resolve.exports';
import semver from 'semver';
import { assertLiteral } from '../config-resolvers/types.js';
import type { PackageManifest, ModuleIdentity, ModuleLock, MetadataSidecar, StaticInspection } from './types.js';
export function nameOf(specifier: string) {
    if (specifier.startsWith('.') || specifier.startsWith('/') || specifier.includes(':'))
        throw Error(`PACKAGE_SPECIFIER ${specifier}`);
    return specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0]!;
}
export function locate(name: string, baseURL: string) {
    for (const folder of createRequire(baseURL).resolve.paths(name) ?? []) {
        const dir = join(folder, name);
        if (existsSync(join(dir, 'package.json')))
            return realpathSync(dir);
    }
    throw Error(`PACKAGE_MISSING ${name} from ${baseURL}`);
}
export function exported(manifest: PackageManifest, entry: string, directory: string) {
    const targets = resolveExports(manifest as Parameters<typeof resolveExports>[0], entry);
    if (!targets?.length)
        throw Error(`EXPORT_MISSING ${manifest.name}${entry}`);
    const target = resolve(directory, targets[0]!);
    if (relative(directory, target).startsWith('..') || !existsSync(target))
        throw Error(`EXPORT_ARTIFACT ${manifest.name}${entry}: ${target}`);
    return target;
}
export function readManifest(directory: string): PackageManifest { return JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8')); }
export function lockedModule(specifier: string, name: string, directory: string, manifest: PackageManifest, lock: ModuleLock) {
    const selected = lock.packages[name];
    if (!selected || !semver.valid(selected.version) || manifest.version !== selected.version || !selected.gitCommit || typeof selected.dirty !== 'boolean' || !Array.isArray(selected.patches) || selected.hostAbi !== 'society0/host/1')
        throw Error(`SOURCE_LOCK ${specifier} at ${directory}`);
    return selected;
}
export function lockedBundle(specifier: string, name: string, version: string, manifest: PackageManifest, lock: ModuleLock): void {
    const selected = lock.packages[name];
    if (!selected || selected.version !== manifest.version || version !== manifest.version || !selected.gitCommit || typeof selected.dirty !== 'boolean' || !Array.isArray(selected.patches))
        throw Error(`BUNDLE_LOCK ${specifier}`);
}
function sidecar(manifest: PackageManifest, path: string, directory: string): unknown { const value: unknown = JSON.parse(readFileSync(exported(manifest, path, directory), 'utf8')); assertLiteral(value); return value; }
function lockedSourceMatches(metadata: MetadataSidecar, identity: ModuleIdentity): boolean {
    return metadata.source?.gitCommit === identity.source.gitCommit
        && metadata.source?.dirty === identity.source.dirty
        && JSON.stringify(metadata.source.patches) === JSON.stringify(identity.source.patches);
}
function metadataIdentity(metadata: MetadataSidecar, identity: ModuleIdentity): void {
    if (metadata.format !== 'society-plugin-metadata/v1' || metadata.package?.name !== identity.package || metadata.package.version !== identity.version || !Array.isArray(metadata.entries) || !lockedSourceMatches(metadata, identity))
        throw Error(`METADATA_IDENTITY ${identity.specifier}`);
}
function sidecarFormats(configSchema: NonNullable<StaticInspection['configSchema']>, api: NonNullable<StaticInspection['api']>, identity: ModuleIdentity): void {
    if (configSchema.format !== 'society-config-schema/v1' || !Array.isArray(configSchema.limitations) || api.format !== 'society-plugin-api/v1' || !Array.isArray(api.entries))
        throw Error(`SIDECAR_FORMAT ${identity.specifier}`);
}
function metadataExports(metadata: MetadataSidecar, manifest: PackageManifest, identity: ModuleIdentity): void {
    for (const entry of metadata.entries) {
        exported(manifest, entry.exportPath, identity.directory);
        if (entry.metadata.hostAbi !== identity.source.hostAbi)
            throw Error(`METADATA_ABI ${identity.specifier}`);
    }
}
export function inspectSidecars(identity: ModuleIdentity): StaticInspection {
    const manifest = readManifest(identity.directory), paths = manifest.society?.plugin;
    if (!paths)
        return { mode: 'manifest-only', identity, limitations: ['MISSING_STATIC_SIDECARS'] };
    const metadata = sidecar(manifest, paths.metadata, identity.directory) as MetadataSidecar, configSchema = sidecar(manifest, paths.configSchema, identity.directory) as NonNullable<StaticInspection['configSchema']>, api = sidecar(manifest, paths.api, identity.directory) as NonNullable<StaticInspection['api']>;
    metadataIdentity(metadata, identity);
    sidecarFormats(configSchema, api, identity);
    metadataExports(metadata, manifest, identity);
    return { mode: 'manifest-only', identity, metadata, configSchema, api, limitations: configSchema.limitations.map(item => JSON.stringify(item)) };
}
