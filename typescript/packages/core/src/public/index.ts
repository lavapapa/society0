import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    mount: Schema.string().default('/public'),
    renderCacheBytes: Schema.natural().min(1).default(268435456),
    cacheDir: Schema.string().default(''),
    searchIndex: Schema.union(['trigram', 'none']).default('trigram'),
    changesPerception: Schema.union(['off', 'digest', 'percept']).default('digest')
});
export const metadata: PluginMetadata = {
    "title": "public",
    "summary": "推式版本化公开资料仓库：规范JSONL行、as-of读取、受众、写指令与trigram候选索引。",
    "configVersion": 1,
    "configPolicy": [
        { "path": "/mount", "mode": "new-run", "affectsResults": true, "secret": false },
        { "path": "/renderCacheBytes", "mode": "boundary", "affectsResults": false, "secret": false },
        { "path": "/cacheDir", "mode": "recreate", "affectsResults": false, "secret": false },
        { "path": "/searchIndex", "mode": "recreate", "affectsResults": false, "secret": false },
        { "path": "/changesPerception", "mode": "boundary", "affectsResults": true, "secret": false }
    ],
    "scope": "generation",
    "allowedScopes": ["generation"],
    "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./public",
    "provides": [{ "key": "public", "protocol": "public", "major": 1, "capabilities": [] }],
    "requires": [
        { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" },
        { "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" },
        { "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }
    ]
};
import { Service } from '@society0/cordis';
import type { Context } from '@society0/cordis';
import { join } from 'node:path';
import type { SqliteTransaction } from '../sqlite/contracts.js';
import type { PublicRegistry, PublicWriter, PublicDiagnostics } from '../contracts/public.js';
import { Registry } from './registry.js';
import { publicWriter } from './writer.js';
import { AudienceResolver } from './audience.js';
import { RenderCache } from './cache.js';
import { PublicMount } from './mount.js';
import '../sqlite/index.js';
import '../artifacts-local/index.js';

export interface PublicOptions {
    readonly mount: string;
    readonly renderCacheBytes: number;
    readonly cacheDir: string;
    readonly searchIndex: 'trigram' | 'none';
    readonly changesPerception: 'off' | 'digest' | 'percept';
}
declare module '@society0/cordis' {
    interface Context {
        public: PublicRepository;
    }
}
export class PublicRepository extends Service {
    static Config = Config;
    static inject = ['slots', 'database', 'artifacts'];
    readonly registry: PublicRegistry;
    readonly cache: RenderCache;
    readonly audiences: AudienceResolver;
    readonly mount: PublicMount;
    constructor(ctx: Context, readonly options: PublicOptions) {
        super(ctx, 'public');
        this.registry = new Registry(ctx);
        const registry = this.registry as Registry;
        this.cache = new RenderCache(options.cacheDir || join(ctx.database.directory, 'public-cache'), options.renderCacheBytes);
        this.audiences = new AudienceResolver(() => [...registry.audiences.values()]);
        this.mount = new PublicMount({
            prefix: options.mount,
            ctx,
            cache: this.cache,
            audiences: this.audiences,
            tables: registry.tables,
            docs: registry.docs,
            momentViews: () => ctx.get('momentViews', false)
        });
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
    writer(tx: SqliteTransaction): PublicWriter {
        const registry = this.registry as Registry;
        return publicWriter(tx, registry.tables, registry.docs);
    }
    /** fork/恢复/重开后从 public_rows 全量重建 trigram 索引；FTS 不进入 changeset。 */
    async rebuildSearch(tx: SqliteTransaction): Promise<void> {
        await tx.execute({ sql: "INSERT INTO public_fts(public_fts) VALUES('delete-all')" });
        await tx.execute({ sql: 'INSERT INTO public_fts(rowid,line) SELECT rowid,line FROM public_rows' });
        await tx.execute({ sql: "INSERT INTO public_search_state(public_search_state) VALUES('delete-all')" });
        await tx.execute({ sql: "INSERT INTO public_search_state(rowid,state) VALUES(?,'ok')", parameters: [BigInt(tx.baseRevision) + 1n] });
    }
    diagnostics(): PublicDiagnostics {
        return { cache: this.cache.diagnostics(), audiences: this.audiences.size };
    }
}
export default PublicRepository;
export { publicMigration } from './schema.js';
export * from './canonical.js';
export { readRows, readFile, readFiles, readNode, readChildren, renderRows } from './store.js';
export { AudienceResolver } from './audience.js';
export { RenderCache } from './cache.js';
export { PublicMount, etagOf } from './mount.js';
export type { Public, PublicTableDecl, PublicDocDecl, PublicWriter, PublicRegistry, PublicAudienceProvider, PublicFile, PublicNode, PublicRow, PublicDiagnostics, PublicCacheDiagnostics } from '../contracts/public.js';
