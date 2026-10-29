/** 主体的统一探索空间；领域SQL与对象仓库继续拥有真实数据。 */
import type { Disposer, Invocation, Json, Lease } from './common.js';
import type { DiscoveryScope, DomainVersion, Page, PageRequest, PayloadRange, StoredValue } from './data.js';
import type { SqliteReadView } from '../sqlite/contracts.js';
import type { ActionExecutionContext } from './domain.js';
export interface ExplorationNode {
    readonly path: string;
    readonly objectId: string;
    readonly kind: 'directory' | 'file' | 'collection';
    readonly versions: readonly DomainVersion[];
    readonly rawBytes?: string;
    readonly querySchema?: Json;
    readonly provenance?: Json;
}
export interface ExplorationMatch {
    readonly path: string;
    readonly objectId: string;
    readonly versions: readonly DomainVersion[];
    readonly rawOffset: string;
    readonly text: string;
}
export interface ExplorationMount {
    readonly id: string;
    readonly prefix: string;
    /** read/list/search/collect共用这个主体范围及实际领域权限。 */
    visible(path: string, scope: DiscoveryScope, reader: SqliteReadView): Promise<boolean>;
    stat(path: string, scope: DiscoveryScope, reader: SqliteReadView): Promise<ExplorationNode>;
    list(path: string, scope: DiscoveryScope, reader: SqliteReadView, request: PageRequest): Promise<Page<ExplorationNode>>;
    /** 返回pin后的原文读取句柄；调用方关闭SQL快照后再流式读取。 */
    open(node: ExplorationNode, scope: DiscoveryScope, reader: SqliteReadView): Promise<Lease<ExplorationContent>>;
    collect(path: string, query: Json, scope: DiscoveryScope, reader: SqliteReadView, request: PageRequest): Promise<Page<StoredValue>>;
}
export type ExplorationMutation = {
    readonly kind: 'mkdir';
    readonly path: string;
    readonly expected: null;
    readonly expectedParent: ExplorationNode;
} | {
    readonly kind: 'remove';
    readonly path: string;
    readonly expected: ExplorationNode;
}
/** targetExpected=null要求目标不存在；给出节点表示明确替换该版本。 */
 | {
    readonly kind: 'rename';
    readonly from: string;
    readonly to: string;
    readonly expected: ExplorationNode;
    readonly targetExpected: ExplorationNode | null;
    readonly expectedParent: ExplorationNode;
};
export interface WritableExplorationMount extends ExplorationMount {
    writeFile(path: string, bytes: AsyncIterable<Uint8Array>, expected: ExplorationNode | null, scope: DiscoveryScope, context: ActionExecutionContext, provenance?: Json): Promise<ExplorationNode>;
    mutate(request: ExplorationMutation, scope: DiscoveryScope, context: ActionExecutionContext): Promise<void>;
}
export interface ExplorationContent {
    readonly node: ExplorationNode;
    read(range: PayloadRange, signal: AbortSignal): AsyncIterable<Uint8Array>;
}
export interface ExplorationSession {
    readonly scope: DiscoveryScope;
    stat(path: string): Promise<ExplorationNode>;
    list(path: string, request: PageRequest): Promise<Page<ExplorationNode>>;
    find(request: {
        readonly root: string;
        readonly glob: string;
        readonly page: PageRequest;
    }): Promise<Page<ExplorationNode>>;
    /** expected固定对象/版本；期间变化返回stale，禁止静默读取另一版本。 */
    open(expected: ExplorationNode): Promise<Lease<ExplorationContent>>;
    grep(request: {
        readonly root: string;
        readonly pattern: string;
        readonly mode: 'literal' | 'regex';
        readonly page: PageRequest;
    }): Promise<Page<ExplorationMatch>>;
    collect(path: string, query: Json, request: PageRequest): Promise<Page<StoredValue>>;
    /** 查询结果显式保存到当前主体/results，返回可继续读取的稳定对象。 */
    saveResult(name: string, rows: AsyncIterable<Json>, context: ActionExecutionContext, provenance?: Json): Promise<ExplorationNode>;
    /** 可写路径限定当前主体workspace/results；对象封存后由受资格检查的短事务更新目录。 */
    writeFile(path: string, bytes: AsyncIterable<Uint8Array>, expected: ExplorationNode | null, context: ActionExecutionContext): Promise<ExplorationNode>;
    mutate(request: ExplorationMutation, context: ActionExecutionContext): Promise<void>;
}
export interface Exploration {
    register(mount: ExplorationMount): Disposer;
    acquire(scope: DiscoveryScope, context: Invocation): Promise<Lease<ExplorationSession>>;
}
export interface ExplorationShell {
    /** 采用成熟shell解释器及同一Exploration文件适配；不开放宿主原生shell。 */
    execute(command: string, session: ExplorationSession, context: ActionExecutionContext): Promise<{
        readonly exitCode: number;
        readonly stdout: string;
        readonly stderr: string;
        readonly results: readonly ExplorationNode[];
    }>;
}
