type SqliteReader = SqliteReadView | SqliteTransaction;
/** 感知材料、持久进度和输入链接；所有Driver共用。 */
import type { Disposer, Invocation, Json, Lease } from './common.js';
import type { DomainVersion, PageBounds, StoredValue } from './data.js';
import type { Message } from './domain.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
export interface PerceptionScope {
    readonly actorId: string;
    readonly momentId: string;
    readonly threadId: string;
    /** 原样保留通用clock值；具体来源解释并验证支持的时标。 */
    readonly logicalTime?: Json;
    readonly reason: Json;
}
export interface Percept {
    readonly id: string;
    readonly kind: string;
    readonly material: StoredValue;
    readonly provenance: Json;
}
export interface PerceptionPage {
    readonly items: readonly Percept[];
    readonly nextCursor: Json;
    /** 固定本轮读取的稳定前缀；hasMore=true时继续读取此前缀。 */
    readonly through: Json;
    readonly hasMore: boolean;
}
export interface PerceptionPreparation {
    readonly material: StoredValue;
    readonly versions: readonly DomainVersion[];
}
export interface PerceptionSource {
    readonly id: string;
    readonly schema: Json;
    readonly cursorScope: 'actor' | 'thread';
    available(scope: PerceptionScope, reader: SqliteReader): Promise<boolean>;
    versions(scope: PerceptionScope, reader: SqliteReader): Promise<readonly DomainVersion[]>;
    /** 可选外部准备在读取快照之外完成，产物及相关版本显式传给collect。 */
    prepare?(scope: PerceptionScope, bounds: PageBounds, context: Invocation): Promise<PerceptionPreparation>;
    /** 有界读取同一借用视图；先复查prepared版本，不持有SQL快照等待外部服务。 */
    collect(scope: PerceptionScope, after: Json | null, through: Json | null, reader: SqliteReadView, bounds: PageBounds, prepared?: PerceptionPreparation): Promise<PerceptionPage>;
}
export interface PerceptionRead {
    readonly sourceId: string;
    readonly registrationId: string;
    readonly cursorScope: 'actor' | 'thread';
    readonly before: Json | null;
    readonly page: PerceptionPage;
    readonly versions: readonly DomainVersion[];
}
export interface PerceptionBatch {
    readonly id: string;
    readonly scope: PerceptionScope;
    readonly reads: readonly PerceptionRead[];
}
export interface PerceptionInput {
    readonly sourceId: string;
    readonly perceptIds: readonly string[];
    /** 大正文编码/对象封存在事务外完成，保留原始材料引用。 */
    readonly message: Message | import('./domain.js').PreparedMessage;
    readonly contexts: readonly StoredValue[];
}
export interface PerceptionSelection {
    /** 服务创建并关闭短snapshot；来源顺序由显式配置确定。 */
    collect(scope: PerceptionScope, bounds: PageBounds, context: Invocation): Promise<PerceptionBatch>;
    /** 复查注册身份、来源版本与before；原子更新actor/thread游标、输入及原文链接。 */
    commit(tx: SqliteTransaction, batch: PerceptionBatch, inputs: readonly PerceptionInput[]): Promise<void>;
}
export interface Perceptions {
    register(source: PerceptionSource): Disposer;
    acquire(ids: readonly string[], context: Invocation): Promise<Lease<PerceptionSelection>>;
}
