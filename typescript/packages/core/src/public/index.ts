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
import type { DiscoveryScope, Page, PageRequest } from '../contracts/data.js';
import type { PerceptionScope, PerceptionSource } from '../contracts/perception.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import type { PublicRegistry, PublicWriter, PublicDiagnostics, PublicChange } from '../contracts/public.js';
import type { PublicChangeEntry, PublicChangeReader } from './changes.js';
import { changeEntries, publicChangesSource, readChanges } from './changes.js';
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
    private readonly stats = { searchStale: 0, fallbackScans: 0 };
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
            momentViews: () => ctx.get('momentViews', false),
            searchOptions: () => {
                const exploration = ctx.get('exploration', false);
                if (!exploration)
                    throw new Error('EXPLORATION_SEARCH_CONFIG');
                return exploration.limits;
            },
            stats: this.stats
        });
    }
    async [Service.init]() {
        const exploration = this.ctx.get('exploration', false);
        if (exploration)
            this.ctx.effect(() => {
                const dispose = exploration.register(this.mount);
                return () => dispose();
            });
        if (this.options.changesPerception === 'off')
            return;
        const perceptions = this.ctx.get('perceptions', false) as { register(source: PerceptionSource): () => void } | false | undefined;
        if (perceptions)
            this.ctx.effect(() => perceptions.register(this.changesSource()), 'public.changes-perception');
    }
    writer(tx: SqliteTransaction): PublicWriter {
        const registry = this.registry as Registry;
        return publicWriter(tx, registry.tables, registry.docs);
    }
    private changeReader(): PublicChangeReader {
        const registry = this.registry as Registry;
        return { tables: registry.tables, docs: registry.docs, defaultNotify: this.options.changesPerception === 'percept' ? 'percept' : 'digest' };
    }
    private async revision(scope: { readonly momentId: string }, view: SqliteReadView | SqliteTransaction): Promise<string> {
        const service = this.ctx.get('momentViews', false);
        if (service) {
            const value = await service.revision(scope.momentId, view as SqliteReadView);
            if (value !== null)
                return value;
        }
        return 'baseRevision' in view ? view.baseRevision : view.revision;
    }
    private async entries(view: SqliteReadView, scope: PerceptionScope, from: string, to: string): Promise<readonly PublicChangeEntry[]> {
        const agent = await this.audiences.resolve(scope, view);
        return changeEntries(view, scope, from, to, this.changeReader(), agent);
    }
    private changesSource(): PerceptionSource {
        return publicChangesSource({
            enabled: this.options.changesPerception !== 'off',
            revision: (scope, view) => this.revision(scope, view),
            read: (view, scope, from, to) => this.entries(view, scope, from, to)
        });
    }
    /** 读取 (from,to] 的变更；自行开短快照并解析受众。 */
    async changes(scope: DiscoveryScope, from: string, to: string, request?: PageRequest): Promise<Page<PublicChange>> {
        const view = await this.ctx.database.snapshot({ maxAgeMs: 30000 });
        try {
            const agent = await this.audiences.resolve(scope, view);
            return await readChanges(view, scope, from, to, this.changeReader(), agent, request);
        }
        finally {
            await view.close();
        }
    }
    /** fork/恢复/重开后从 public_rows 全量重建 trigram 索引；FTS 不进入 changeset。 */
    async rebuildSearch(tx: SqliteTransaction): Promise<void> {
        await tx.execute({ sql: "INSERT INTO public_fts(public_fts) VALUES('delete-all')" });
        await tx.execute({ sql: 'INSERT INTO public_fts(rowid,line) SELECT rowid,line FROM public_rows' });
        await tx.execute({ sql: "INSERT INTO public_search_state(public_search_state) VALUES('delete-all')" });
        await tx.execute({ sql: "INSERT INTO public_search_state(rowid,state) VALUES(?,'ok')", parameters: [BigInt(tx.baseRevision) + 1n] });
    }
    diagnostics(): PublicDiagnostics {
        return { cache: this.cache.diagnostics(), audiences: this.audiences.size, searchStale: this.stats.searchStale, fallbackScans: this.stats.fallbackScans };
    }
}
export default PublicRepository;
export { publicMigration } from './schema.js';
export * from './canonical.js';
export { readRows, readFile, readFiles, readNode, readChildren, renderRows } from './store.js';
export { AudienceResolver } from './audience.js';
export { RenderCache } from './cache.js';
export { PublicMount, etagOf } from './mount.js';
export { CHANGE_FILES_SQL, CHANGE_DOCS_SQL, CHANGE_ROWS_SQL, changeEntries, publicChangesSource, readChanges } from './changes.js';
export type { PublicChangeEntry, PublicChangeReader, PublicChangesSourceHost } from './changes.js';
export type { Public, PublicTableDecl, PublicDocDecl, PublicWriter, PublicRegistry, PublicAudienceProvider, PublicAudienceScope, PublicFile, PublicNode, PublicRow, PublicChange, PublicChangeRows, PublicDiagnostics, PublicCacheDiagnostics } from '../contracts/public.js';
