import Schema from '@society0/schemastery';
import { hostMetadata, hostApi } from './metadata.js';
import { Context, Service, type Plugin } from '@society0/cordis';
import { pathToFileURL } from 'node:url';
import { preflight, type PreflightSession } from '../packages.js';
export const Config = Schema.object({ baseURL: Schema.string() });
export const metadata = hostMetadata('module-resolver', 'Resolve pinned package exports and verify source identity', [{ path: '/baseURL', mode: 'recreate', affectsResults: false }]);
export const api = hostApi('./module-resolver', ["moduleResolver"], ["diagnostics"]);
export * from './types.js';
export { locate, readManifest } from './package-files.js';
import type { ModuleLock, ModuleIdentity, StaticInspection, VerifiedModule } from './types.js';
import { nameOf, locate, exported, readManifest, lockedModule, lockedBundle, inspectSidecars } from './package-files.js';
function normalize(namespace: Record<string, unknown>, specifier: string): Plugin {
    if ('default' in namespace && 'apply' in namespace)
        throw Error(`PLUGIN_ENTRY_AMBIGUOUS ${specifier}`);
    const plugin = namespace.default ?? namespace;
    if (typeof plugin === 'function' || plugin && typeof plugin === 'object' && typeof Reflect.get(plugin, 'apply') === 'function')
        return plugin as Plugin;
    throw Error(`PLUGIN_ENTRY_INVALID ${specifier}`);
}
declare module '@society0/cordis' {
    interface Context {
        moduleResolver: ModuleResolver;
    }
}
export class ModuleResolver extends Service {
    static Config = Config;
    static inject = ['diagnostics'];
    private readonly verified = new WeakSet<ModuleIdentity>();
    constructor(ctx: Context, readonly config: {
        baseURL?: string;
    } = {}) { super(ctx, 'moduleResolver'); }
    private baseURL(): string { return this.config.baseURL ?? this.ctx.baseUrl ?? import.meta.url; }
    resolve(specifier: string, lock: ModuleLock, session?: PreflightSession): ModuleIdentity {
        const baseURL = this.baseURL();
        const name = nameOf(specifier);
        const directory = locate(name, baseURL);
        const manifest = readManifest(directory);
        const selected = lockedModule(specifier, name, directory, manifest, lock);
        const exportPath = specifier === name ? '.' : '.' + specifier.slice(name.length);
        const file = exported(manifest, exportPath, directory);
        const [checked] = session ? session.preflight([specifier], baseURL) : preflight([specifier], baseURL);
        if (checked !== pathToFileURL(file).href)
            throw Error(`EXPORT_RESOLUTION_MISMATCH ${specifier}`);
        const identity = Object.freeze({ specifier, package: name, version: manifest.version, exportPath, url: pathToFileURL(file).href, directory, source: Object.freeze({ package: name, version: manifest.version, gitCommit: selected.gitCommit, dirty: selected.dirty, lockfile: lock.lockfile, patches: Object.freeze([...selected.patches]), hostAbi: selected.hostAbi }) });
        this.verified.add(identity);
        return identity;
    }
    bundle(specifier: string, version: string, lock: ModuleLock): {
        url: string;
        version: string;
    } {
        const baseURL = this.baseURL();
        const name = nameOf(specifier);
        const directory = locate(name, baseURL);
        const manifest = readManifest(directory);
        lockedBundle(specifier, name, version, manifest, lock);
        const entry = specifier === name ? manifest.society?.bundle?.patch : '.' + specifier.slice(name.length);
        if (!entry)
            throw Error(`BUNDLE_PATCH_MISSING ${specifier}`);
        return { url: pathToFileURL(exported(manifest, entry, directory)).href, version: manifest.version };
    }
    inspect(identity: ModuleIdentity): StaticInspection {
        if (!this.verified.has(identity))
            throw Error('UNVERIFIED_MODULE_IDENTITY');
        return inspectSidecars(identity);
    }
    async importVerified(identity: ModuleIdentity): Promise<VerifiedModule> {
        if (!this.verified.has(identity))
            throw Error('UNVERIFIED_MODULE_IDENTITY');
        try {
            const namespace: Record<string, unknown> = await import(identity.url);
            return { identity, namespace, plugin: normalize(namespace, identity.specifier) };
        }
        catch (error) {
            throw Error(`RUNTIME_IMPORT ${identity.specifier} at ${identity.url}: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
        }
    }
}
export default ModuleResolver;
