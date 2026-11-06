/** 按 moment 冻结的读视图：as-of revision 与固定快照。 */
import type { Invocation, Lease } from './common.js';
import type { SqliteDatabase } from '../sqlite/index.js';
import type { QueryBounds, SqliteReadView, SqliteRow, SqliteStatement, SqliteTransaction } from '../sqlite/contracts.js';
import type { WriteLease } from '../execution-fence/index.js';

export interface MomentView {
    readonly momentId: string;
    /** R_m：本 moment 的冻结 revision。 */
    readonly revision: string;
    /** false = live 模式；重启或降级时不得静默替换。 */
    readonly frozen: boolean;
    /** 版本化表 as-of 读取；内部借用租约持有的短快照。 */
    query(statement: SqliteStatement, bounds: QueryBounds): Promise<readonly SqliteRow[]>;
    /** 本 moment 的固定快照（派生渲染用）；由租约持有并在 release 时关闭。 */
    pinned(): Promise<SqliteReadView>;
}
/** 冻结视图 + 本主体实时写入：共享数据用 R_m，自身域事实用实时短快照。 */
export interface MomentReadModel {
    /** 版本化表按 R_m 冻结读取。 */
    readonly frozen: MomentView;
    /** 每次调用打开新短快照，包含此前已提交的本主体写入（read-your-writes）。 */
    self(): Promise<SqliteReadView>;
}
export interface MomentViews {
    /** 在调用方事务内记录 R_m；已存在时保持不变。 */
    open(tx: SqliteTransaction, momentId: string): Promise<string>;
    acquire(scope: { readonly momentId: string }, context: Invocation & { readonly writeLease?: WriteLease; readonly database?: SqliteDatabase }): Promise<Lease<MomentView>>;
    revision(momentId: string, reader: SqliteReadView): Promise<string | null>;
    /** 本主体视图（INTEGRATION §2.2）：frozen 用 R_m，self() 实时可见自身写入。 */
    read(scope: { readonly momentId: string }, context: Invocation & { readonly writeLease?: WriteLease; readonly database?: SqliteDatabase }): Promise<Lease<MomentReadModel>>;
}
