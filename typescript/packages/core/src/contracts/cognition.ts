/** 生产契约：embedding 可选，记忆 SQL 为原始向量与索引进度的依据。 */
import type { Disposer, Invocation, Json, Lease } from './common.js';
import type { ArtifactRef } from '../artifacts-local/store.js';
import type { AttemptIdentity } from '../attempts/ledger.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import type { Page, PageRequest } from './data.js';
import type { ActivationContext } from './runtime.js';
import type { ThreadView } from './domain.js';

export interface EmbeddingIdentity {
  readonly provider: string;
  readonly configId: string;
  readonly model: string;
  readonly dimensions: number;
}
export interface EmbeddingOrigin { readonly attempt: AttemptIdentity; readonly itemIndex: number }
export interface EmbeddingUse {
  readonly inputIndex: number;
  readonly vector: readonly number[];
  readonly origin: EmbeddingOrigin;
  readonly reused: 'none' | 'singleflight' | 'cache';
}
export interface EmbeddingRequest { readonly identity: EmbeddingIdentity; readonly texts: readonly string[] }
export interface EmbeddingLimits {
  readonly maxTextBytes: number;
  readonly maxPendingBytes: number;
  readonly maxBatchItems: number;
  readonly maxBatchBytes: number;
  readonly maxWaitMs: number;
  readonly maxCacheItems: number;
  readonly maxCacheBytes: number;
  readonly scheduling: 'fifo';
}
export interface EmbeddingProvider {
  readonly identity: EmbeddingIdentity;
  /** 物理输入已合批，itemIndex 对应物理批次位置；成本写入该 attempt。抛错可附带 physical:'not_dispatched'|'failed'|'response'；缺省视为unknown。 */
  embed(texts: readonly string[], attempt: AttemptIdentity, signal: AbortSignal): Promise<{ readonly items: readonly { readonly itemIndex: number; readonly vector: readonly number[] }[]; readonly usage: Json; readonly original: Json | ArtifactRef }>;
  close(): Promise<void>;
}
export interface Embeddings {
  /** 缓存、FIFO 队列和物理 attempt 归本 run；拒绝其他 run 的 Invocation。 */
  readonly runId: string;
  register(provider: EmbeddingProvider, limits: EmbeddingLimits): Disposer;
  /** 按 identity.provider 注册名读取不可变描述；未登记时报 PROVIDER_UNAVAILABLE。 */
  identity(provider: string): EmbeddingIdentity;
  /** 取消此 Invocation 的等待者；其他共享等待者继续使用原物理任务。 */
  embed(request: EmbeddingRequest, context: Invocation): Promise<readonly EmbeddingUse[]>;
  close(): Promise<void>;
}
export interface Experience { readonly id: string; readonly actorId: string; readonly threadId: string; readonly body: Json; readonly artifacts: readonly ArtifactRef[] }
export interface MemoryVector { readonly identity: EmbeddingIdentity; readonly vector: readonly number[]; readonly origin: EmbeddingOrigin }
export interface MemoryChangeCursor { readonly revision: Revision; readonly id: string }
export type MemoryChange =
  | { readonly kind: 'upsert'; readonly cursor: MemoryChangeCursor; readonly actorId: string; readonly vector: MemoryVector }
  | { readonly kind: 'delete'; readonly cursor: MemoryChangeCursor; readonly actorId: string };
export type VectorDistance = 'cosine' | 'euclidean' | 'dot';
export interface MemoryIndexProgress {
  readonly runId: string;
  readonly space: string;
  readonly distance: VectorDistance;
  readonly generation: string;
  readonly identity: EmbeddingIdentity;
  readonly after?: MemoryChangeCursor;
  readonly throughRevision: Revision;
}
export interface MemoryRepository {
  read(snapshot: SqliteReadView, id: string): Promise<Experience | undefined>;
  append(tx: SqliteTransaction, experience: Experience, vector?: MemoryVector): Promise<void>;
  remove(tx: SqliteTransaction, id: string): Promise<void>;
  scan(snapshot: SqliteReadView, request: PageRequest): Promise<Page<Experience>>;
  changes(snapshot: SqliteReadView, request: PageRequest & { readonly after?: MemoryChangeCursor; readonly throughRevision: Revision }): Promise<Page<MemoryChange>>;
  progress(snapshot: SqliteReadView, generation: string): Promise<MemoryIndexProgress | undefined>;
  recordProgress(tx: SqliteTransaction, progress: MemoryIndexProgress): Promise<void>;
}
export interface MemoryIndex {
  readonly runId: string;
  readonly space: string;
  readonly distance: VectorDistance;
  readonly generation: string;
  readonly identity: EmbeddingIdentity;
  /** 稳定 ID 的 upsert/delete 可重试；外部执行结束后才能记录 SQL 进度。 */
  apply(changes: readonly MemoryChange[], signal: AbortSignal): Promise<void>;
  query(vector: readonly number[], limit: number, progress: MemoryIndexProgress, signal: AbortSignal): Promise<readonly { readonly id: string; readonly distance: number }[]>;
  reset(signal: AbortSignal): Promise<void>;
  close(): Promise<void>;
}
export interface MemoryIndexes { register(name: string, index: MemoryIndex): Disposer; acquire(name: string): Promise<Lease<MemoryIndex>> }
export interface MemoryWritePolicy { extract(thread: ThreadView, context: ActivationContext): Promise<readonly Experience[]> }

type Revision = string;
