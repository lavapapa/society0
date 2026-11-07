import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ mount: Schema.string().default('/self') });
export const metadata: PluginMetadata = {
    "title": "public-self",
    "summary": "按主体实时读取的只读 /self 视图与 SelfProvider 槽；写入后同一 moment 立即可见。",
    "configVersion": 1,
    "configPolicy": [{ "path": "/mount", "mode": "new-run", "affectsResults": true, "secret": false }],
    "scope": "generation",
    "allowedScopes": ["generation"],
    "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./public-self",
    "provides": [{ "key": "publicSelf", "protocol": "publicSelf", "major": 1, "capabilities": [] }],
    "requires": [
        { "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" },
        { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" },
        { "service": { "key": "exploration", "protocol": "exploration", "major": 1, "capabilities": [] }, "optional": true, "phase": "module" }
    ]
};
import { Service } from '@society0/cordis';
import type { Context } from '@society0/cordis';
import type { Disposer } from '../contracts/common.js';
import type { PublicSelf, SelfProvider, SelfRegistry as SelfRegistryContract } from '../contracts/public-self.js';
import { SelfRegistry as Registry } from './registry.js';
import { SelfCache } from './cache.js';
import { PublicSelfMount } from './mount.js';
import '../sqlite/index.js';

export interface PublicSelfOptions { readonly mount: string }
declare module '@society0/cordis' {
    interface Context {
        publicSelf: PublicSelfService;
    }
}
export class PublicSelfService extends Service implements PublicSelf {
    static Config = Config;
    static inject = ['database', 'slots'];
    readonly registry: SelfRegistryContract;
    readonly cache: SelfCache;
    readonly mount: PublicSelfMount;
    constructor(ctx: Context, readonly options: PublicSelfOptions) {
        super(ctx, 'publicSelf');
        if (typeof options.mount !== 'string' || !options.mount.startsWith('/') || options.mount === '/' || options.mount.endsWith('/'))
            throw Object.assign(new Error('SELF_MOUNT: ' + String(options.mount)), { code: 'SELF_MOUNT' });
        const registry = new Registry(ctx);
        this.registry = registry;
        this.cache = new SelfCache();
        this.mount = new PublicSelfMount({ prefix: options.mount, ctx, providers: registry.providers, cache: this.cache });
    }
    async [Service.init]() {
        const exploration = this.ctx.get('exploration', false);
        if (!exploration)
            return;
        this.ctx.effect(() => {
            const dispose = exploration.register(this.mount);
            return () => dispose();
        });
    }
    provide(provider: SelfProvider): Disposer {
        const prefix = this.options.mount;
        if (!provider.path.startsWith(prefix + '/'))
            throw Object.assign(new Error('SELF_PROVIDER_PATH: ' + String(provider.path)), { code: 'SELF_PROVIDER_PATH' });
        return this.registry.provide(provider);
    }
    diagnostics() { return this.cache.diagnostics(); }
}
export default PublicSelfService;
export { PublicSelfMount } from './mount.js';
export { SelfCache } from './cache.js';
export { selfProviderSlot, SelfRegistry } from './registry.js';
export { canonicalLine, canonicalRow, normalizeDecimal } from '../public/canonical.js';
export type { PublicSelf, SelfProvider } from '../contracts/public-self.js';
