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
import type { SqliteTransaction } from '../sqlite/contracts.js';
import type { PublicRegistry, PublicWriter } from '../contracts/public.js';
import { Registry } from './registry.js';
import { publicWriter } from './writer.js';
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
    constructor(ctx: Context, readonly options: PublicOptions) {
        super(ctx, 'public');
        this.registry = new Registry(ctx);
    }
    writer(tx: SqliteTransaction): PublicWriter {
        const registry = this.registry as Registry;
        return publicWriter(tx, registry.tables, registry.docs);
    }
}
export default PublicRepository;
export { publicMigration } from './schema.js';
export * from './canonical.js';
export { readRows, readFile, readFiles, readNode, readChildren, renderRows } from './store.js';
export type { Public, PublicTableDecl, PublicDocDecl, PublicWriter, PublicRegistry, PublicFile, PublicNode, PublicRow } from '../contracts/public.js';
