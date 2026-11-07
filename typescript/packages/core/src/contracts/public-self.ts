/** /self：按 actor 实时读取的只读视图与提供者槽。 */
import type { Disposer } from './common.js';
import type { DomainVersion } from './data.js';
import type { SqliteReadView } from '../sqlite/contracts.js';

export interface SelfProvider {
    /** 挂载内路径，例如 '/self/inventory.jsonl'。 */
    readonly path: string;
    readonly media: string;
    /** 可选：该 actor 是否能看到此路径；缺省总是可见。用于按主体裁剪条目。 */
    visible?(actorId: string, view: SqliteReadView): Promise<boolean>;
    /** 作为缓存键的一部分；只有影响渲染结果的领域版本才应放进这里。 */
    versions(actorId: string, view: SqliteReadView): Promise<readonly DomainVersion[]>;
    render(actorId: string, view: SqliteReadView, signal: AbortSignal): AsyncIterable<Uint8Array>;
}

export interface SelfRegistry {
    provide(provider: SelfProvider): Disposer;
}

export interface PublicSelf {
    readonly registry: SelfRegistry;
}
