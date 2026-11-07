/** 公开资料仓库：推式版本化文件、受众标签与写指令。 */
import type { Disposer, Json } from './common.js';
import type { DiscoveryScope, Page, PageRequest } from './data.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import type { ArtifactRef } from '../artifacts-local/store.js';

/** 粗粒度受众：'all' | 'role:x' | 'region:y'；个体专属数据放 /self。 */
export type Audience = string;
export type PublicNotify = 'none' | 'digest' | 'percept';
export interface PublicShard {
    readonly by: string;
    readonly format: 'day' | 'month' | 'year' | 'value' | 'none';
    /** 覆盖默认文件名；返回值不含 .jsonl。 */
    readonly file?: (value: string) => string;
}
export interface PublicTableDecl {
    readonly path: string;
    readonly title: string;
    readonly description: string;
    /** JSON Schema；properties 的声明顺序即规范行的字段顺序。 */
    readonly schema: Json;
    readonly key: readonly string[];
    readonly sort: readonly { readonly field: string; readonly order?: 'asc' | 'desc' }[];
    readonly shard: PublicShard;
    /** 精确小数字段：字符串入库并按声明规范化，禁止 JS 浮点直写。 */
    readonly decimals?: readonly string[];
    readonly notify?: PublicNotify;
}
export interface PublicDocDecl {
    readonly path: string;
    readonly title: string;
    readonly media: string;
    readonly notify?: PublicNotify;
}
export interface PublicFile {
    readonly file: string;
    readonly parent: string;
    readonly tablePath: string;
    readonly revision: string;
    readonly rows: number;
    readonly bytes: number;
    readonly audiences: readonly Audience[];
}
export interface PublicRow {
    readonly file: string;
    readonly rowKey: string;
    readonly sortKey: string;
    readonly revision: string;
    readonly audience: Audience;
    readonly line: Uint8Array;
}
export interface PublicNode {
    readonly path: string;
    readonly parent: string;
    readonly kind: 'dir' | 'doc' | 'table' | 'derived';
    readonly revision: string;
    readonly provider: string;
    readonly nodeId: string;
    readonly media: string;
    readonly meta: Json;
    readonly audience: Audience;
    readonly body?: Uint8Array;
    readonly artifact?: string;
    readonly bytes?: number;
}
export interface PublicRowWriteOptions {
    readonly audience?: Audience | ((row: Json) => Audience);
}
export interface PublicWriter {
    /** 按 key upsert；同事务内同一行多次写入只保留最后一次。 */
    upsert(table: string, rows: Iterable<Json>, options?: PublicRowWriteOptions): Promise<{ changed: number }>;
    retract(table: string, keys: Iterable<Json>): Promise<{ retracted: number }>;
    replaceShard(table: string, shardValue: string, rows: Iterable<Json>): Promise<{ inserted: number; updated: number; retracted: number }>;
    publish(path: string, body: Uint8Array | { readonly artifact: ArtifactRef }, options?: { readonly media?: string; readonly audience?: Audience; readonly meta?: Json }): Promise<void>;
    unpublish(path: string): Promise<void>;
    setMeta(path: string, patch: Json): Promise<void>;
}
export interface PublicAudienceScope {
    readonly actorId: string;
    readonly momentId: string;
}
export interface PublicAudienceProvider {
    readonly id: string;
    /** 每 actor 每 moment 调用一次；结果缓存在挂载内，必须返回粗粒度受众。 */
    audiences(scope: PublicAudienceScope, view: SqliteReadView): Promise<readonly Audience[]>;
}
export interface PublicRegistry {
    table(decl: PublicTableDecl): Disposer;
    doc(decl: PublicDocDecl): Disposer;
    audience(provider: PublicAudienceProvider): Disposer;
}
export interface PublicCacheDiagnostics {
    readonly renders: number;
    readonly memoryHits: number;
    readonly diskHits: number;
    readonly misses: number;
    readonly entries: number;
    readonly sizes: number;
    readonly bytes: number;
}
export interface PublicDiagnostics {
    readonly cache: PublicCacheDiagnostics;
    readonly audiences: number;
    readonly searchStale: number;
    readonly fallbackScans: number;
}
export interface PublicChangeRows {
    readonly added: number;
    readonly updated: number;
    readonly retracted: number;
}
export interface PublicChange {
    readonly path: string;
    readonly kind: 'added' | 'updated' | 'retracted' | 'meta';
    readonly rows?: PublicChangeRows;
    readonly revision: string;
    readonly notify: 'none' | 'digest' | 'percept';
}
export interface Public {
    readonly registry: PublicRegistry;
    writer(tx: SqliteTransaction): PublicWriter;
    /** (from,to] 内按受众过滤并按路径聚合的变更；只读修订列/索引。 */
    changes(scope: DiscoveryScope, from: string, to: string, request: PageRequest): Promise<Page<PublicChange>>;
}
