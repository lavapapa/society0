import type { WriteLease } from '../execution-fence/index.js';
type OperationId = string;
type Revision = string;
/** 实际响应预算、追加结果及分块正文；实现与性能待验证。 */
import type { Json } from './common.js';
import type { ArtifactRef } from '../artifacts-local/store.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
export interface PageBounds {
    readonly maxItems: number;
    readonly maxResponseBytes: number;
}
export interface PageRequest extends PageBounds {
    readonly cursor?: string;
}
export interface DomainVersion {
    readonly owner: string;
    readonly version: string;
}
export interface DiscoveryScope {
    readonly actorId: string;
    readonly momentId: string;
    readonly logicalTime?: Json;
    readonly query: Json;
}
export interface PageIdentity {
    readonly runId: string;
    readonly actorId: string;
    readonly momentId: string;
    readonly query: Json;
    readonly versions: readonly DomainVersion[];
}
export interface Page<T> {
    readonly items: readonly T[];
    readonly total: string;
    readonly next?: string;
    readonly revision: Revision;
    /** 固定 UTF-8 JSON 编码后的整个响应字节，包含该字段与游标。 */
    readonly responseBytes: number;
}
export interface PayloadRef {
    readonly object: ArtifactRef;
    readonly codec: string;
    readonly rawOffset: string;
    readonly rawBytes: string;
}
export type StoredValue = {
    readonly kind: 'inline';
    readonly value: Json;
} | {
    readonly kind: 'reference';
    readonly ref: PayloadRef;
};
export interface PayloadRange {
    readonly offset: string;
    readonly length: number;
    readonly maxBytes: number;
}
export interface PayloadReader {
    /** 返回未压缩原文的字节流，范围相对于所引用正文。 */
    readPayload(ref: PayloadRef, range: PayloadRange, signal?: AbortSignal): AsyncIterable<Uint8Array>;
}
export interface FrameEntry {
    readonly ordinal: string;
    readonly rawOffset: string;
    readonly rawBytes: number;
    readonly storedOffset: string;
    readonly storedBytes: number;
}
export interface DatasetDescription {
    readonly id: string;
    readonly records: string;
    readonly rawBytes: string;
    readonly codec: string;
}
export interface DatasetRecord {
    readonly ordinal: string;
    readonly value: StoredValue;
}
export interface Datasets extends PayloadReader {
    describe(id: string, snapshot: SqliteReadView): Promise<DatasetDescription>;
    page(id: string, snapshot: SqliteReadView, request: PageRequest): Promise<Page<DatasetRecord>>;
    /** 一遍式消费；对象准备在事务外完成，attach 在调用者事务内登记引用。 */
    prepare(rows: AsyncIterable<Json>, options: {
        readonly maxRecordBytes: number;
        readonly frameBytes: number;
        readonly maxPendingBytes: number;
        readonly signal: AbortSignal;
    }): Promise<ArtifactRef>;
    attach(tx: SqliteTransaction, id: string, object: ArtifactRef): Promise<void>;
}
export interface ResultSetDescription {
    readonly id: string;
    readonly through: string;
    readonly finished: boolean;
    readonly records: string;
}
export interface ResultWriteBounds {
    readonly maxBatchRows: number;
    readonly maxInputBytes: number;
    readonly maxCapturedBytes: number;
    readonly maxRecordBytes: number;
}
export interface ResultRecord {
    readonly ordinal: string;
    readonly value: StoredValue;
}
export interface Results extends PayloadReader {
    /** 服务用多笔短事务写入；正常消费至 EOF 并结算后提交 finished。 */
    write(lease: WriteLease, operationId: OperationId, id: string, rows: AsyncIterable<Json>, bounds: ResultWriteBounds, signal: AbortSignal): Promise<ResultSetDescription>;
    describe(id: string, snapshot: SqliteReadView): Promise<ResultSetDescription>;
    page(id: string, snapshot: SqliteReadView, request: PageRequest & {
        readonly through?: string;
    }): Promise<Page<ResultRecord> & {
        readonly through: string;
        readonly finished: boolean;
    }>;
    current(snapshot: SqliteReadView, request: PageRequest): Promise<Page<{
        readonly name: string;
        readonly value: StoredValue;
    }>>;
    setCurrent(tx: SqliteTransaction, name: string, value: StoredValue): Promise<void>;
}
export interface DatasetCodec {
    readonly id: string;
    encode(rows: AsyncIterable<Json>): AsyncIterable<Uint8Array>;
    decode(bytes: AsyncIterable<Uint8Array>): AsyncIterable<Json>;
}
