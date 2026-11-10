import type { Actor } from '../actors/index.js';
import type { BootstrapContext } from '../initializers/index.js';
type BootstrapPreparationContext = Pick<BootstrapContext, 'runId' | 'schema' | 'initialTime' | 'signal' | 'artifacts'>;
type SqliteReader = SqliteReadView | SqliteTransaction;
/** 领域包拥有实际 SQLite 表、索引与 SQL。 */
import type { Json, Disposer, Invocation, Lease } from './common.js';
import type { ActivationContext, ActivationIntent, ActivationResult } from './runtime.js';
import type { ActionExposure, ActionOffer, ActionRef, ActionSchemaCard, ActionTargets, OfferIdentity, OfferLimits, OfferProvider, OfferSet, TargetRef } from './affordance.js';
import type { ArtifactRef } from '../artifacts-local/store.js';
import type { CommitReceipt, SqliteReadView, SqliteTransaction, TransactionRequest } from '../sqlite/contracts.js';
import type { DiscoveryScope, Page, PageRequest, PayloadReader, StoredValue } from './data.js';
export type { Actor } from '../actors/index.js';
export type { Actors } from '../actors/index.js';
export interface InformationSource {
    readonly id: string;
    readonly schema: Json;
    readonly capabilities: readonly string[];
    available(scope: DiscoveryScope, reader: SqliteReader): Promise<boolean>;
    query(input: Json, scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<StoredValue>>;
}
export interface Information {
    register(source: InformationSource): Disposer;
    discover(scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<Omit<InformationSource, 'query' | 'available'>>>;
    query(id: string, input: Json, scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<StoredValue>>;
}
export interface ActionScope extends DiscoveryScope {
    readonly target?: TargetRef;
}
export interface ActionExecutionContext extends ActivationContext {
    readonly actionRegistrationId: string;
    /** 本次执行的目标对象；无目标行动省略。 */
    readonly target?: TargetRef;
    /** 命中的 offer（来源 provider 和 key）；ambient 行动没有 offer。 */
    readonly offer?: OfferIdentity;
    /** 同一原生写事务开头复查主体及动作资格，再执行全部领域写入。 */
    transaction<T>(request: TransactionRequest, body: (tx: SqliteTransaction) => Promise<T>): Promise<{
        readonly value: T;
        readonly receipt: CommitReceipt;
    }>;
}
/** 行动输入被 schema 拒绝时的声明式帮助；由行动定义自带，Core 不猜示例。 */
export interface ActionInputHelp {
    /** 该行动的最小合法输入，必须能通过自身 inputSchema。 */
    readonly example: Json;
    /** 常见误写字段名 → 应写的字段路径列表，例如 parties → seller/buyer。 */
    readonly fieldAliases?: Readonly<Record<string, readonly string[]>>;
}
export interface ActionDefinition {
    readonly name: string;
    /** 给模型看的一句话说明，计入 schema 摘要。 */
    readonly description?: string;
    readonly inputSchema: Json;
    readonly outputSchema: Json;
    /** 可选：ACTION_INPUT 回执附带的合法示例与字段别名对照。 */
    readonly inputHelp?: ActionInputHelp;
    readonly effect: 'world' | 'workspace' | 'external' | 'read';
    /** offer：参与可供性（默认）；tool：固定工具的执行后端，不进入 offer；internal：只供插件或规则调用。 */
    readonly exposure?: ActionExposure;
    /** 缺省为 none：无目标。无目标行动 ambient 默认 true；有目标行动默认 false。 */
    readonly targets?: ActionTargets;
    readonly ambient?: boolean;
    available(scope: ActionScope, reader: SqliteReader): Promise<boolean>;
    execute(input: Json, context: ActionExecutionContext): Promise<ActionReceipt>;
}
export interface ActionNotification {
    readonly actorId: string;
    readonly reason: 'message' | 'request' | 'mention';
    readonly ref: Json;
}
export interface ActionDelivery {
    readonly recipients: readonly {
        readonly actorId: string;
        readonly visibleRevision: string;
    }[];
    readonly wake: 'scheduled' | 'deferred' | 'none';
}
/** 唤醒策略钩子：C 的激活策略接入；未接入时 wake 保持 none。 */
export interface ActionNotifier {
    readonly id: string;
    /** 第二个参数是通知产生时的激活上下文；旧实现只接收 intent 仍然可用。 */
    deliver(intent: ActivationIntent, context?: ActivationContext): Promise<'scheduled' | 'deferred' | 'none'>;
}
export interface ActionReceipt {
    readonly requiredOperations?: readonly string[];
    readonly operationId: string;
    readonly status: 'accepted' | 'completed' | 'waiting' | 'incomplete' | 'rejected';
    readonly value: Json;
    readonly original: Json;
    readonly artifacts: readonly ArtifactRef[];
    /** 自动恢复重跑时返回原回执：本次没有再执行行动。 */
    readonly replayed?: true;
    /** 重放时本次输入与原回执不同，原回执仍然有效。 */
    readonly inputMismatch?: true;
    /** 领域行动在写事务内声明的唤醒对象；由框架在提交后转交策略。 */
    readonly notify?: readonly ActionNotification[];
    /** 框架填写的送达回执；wake 由策略给出，默认 none。 */
    readonly delivery?: ActionDelivery;
}
export interface ActionSelection {
 discover(scope:DiscoveryScope,view:SqliteReadView,request:PageRequest):Promise<Page<Omit<ActionDefinition,'execute'|'available'>>>;
 execute(name:string,input:Json,context:ActivationContext,target?:TargetRef):Promise<ActionReceipt>;
}
export interface Actions {
    acquire(context:Invocation):Promise<Lease<ActionSelection>>;
    register(definition: ActionDefinition): Disposer;
    discover(scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<Omit<ActionDefinition, 'execute' | 'available'>>>;
    execute(name: string, input: Json, context: ActivationContext, target?: TargetRef): Promise<ActionReceipt>;
    provide(provider: OfferProvider): Disposer;
    provideNotifier(notifier: ActionNotifier): Disposer;
    offers(scope: DiscoveryScope, view: SqliteReadView, limits: OfferLimits): Promise<OfferSet>;
    offersFor(scope: DiscoveryScope, view: SqliteReadView, filter: { readonly targets: readonly TargetRef[]; readonly actions?: readonly string[] }): Promise<readonly ActionRef[]>;
    describe(name: string): Promise<ActionSchemaCard | undefined>;
    inputHelp(name: string): Promise<ActionInputHelp | undefined>;
}
export interface RuleHandler {
    activate(input: Json, context: ActivationContext): Promise<ActivationResult>;
}
export interface Message {
    readonly id: string;
    readonly interactionId: string;
    readonly role: string;
    readonly content: Json | ArtifactRef;
    readonly originalProviderFields: Json | ArtifactRef;
}
export interface PreparedMessage {
    readonly id: string;
    readonly interactionId: string;
    readonly role: string;
    readonly payload: StoredValue;
    readonly references?: readonly ArtifactRef[];
}
export interface ThreadView {
    readonly id: string;
    readonly actorId: string;
    readonly momentId: string;
    readonly interactionId: string;
    readonly headSeq: string;
    readonly messageCount: string;
}
export interface ThreadMessage {
    readonly seq: string;
    readonly id: string;
    readonly role: string;
    readonly payload: StoredValue;
}
export interface ConsumerPosition {
    readonly consumerId: string;
    readonly cursor: Json;
}
export interface Threads extends PayloadReader {
    prepare(message: Message, signal?: AbortSignal): Promise<PreparedMessage>;
    open(actor: string, moment: string, snapshot: SqliteReadView): Promise<ThreadView>;
    append(tx: SqliteTransaction, thread: string, message: Message | PreparedMessage): Promise<void>;
    /** 正文对象在事务外准备，消息引用、consumer cursor 和 context 同事务写入。 */
    appendInput(tx: SqliteTransaction, request: {
        readonly threadId: string;
        readonly expectedConsumer: ConsumerPosition | null;
        readonly nextConsumer: ConsumerPosition;
        readonly message: Message | PreparedMessage;
        readonly contexts: readonly StoredValue[];
    }): Promise<void>;
    consumer(thread: string, consumerId: string, snapshot: SqliteReadView): Promise<ConsumerPosition | undefined>;
    page(thread: string, scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<ThreadMessage>>;
    tail(thread: string, scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<ThreadMessage>>;
}
export interface PromptSection {
    readonly id: string;
    readonly order: number;
    readonly text: (context: ActivationContext) => Promise<string>;
    /** header 进入线程首条 SystemMessage；baseline 在线程首条输入发送一次。默认 header。 */
    readonly scope?: 'header' | 'baseline';
}
export interface PromptToolContribution {
    readonly id: string;
    readonly name: string;
    readonly action: string;
    readonly order: number;
}
export interface PromptAssembly {
    section(spec: PromptSection): Disposer;
    context(spec: PromptSection): Disposer;
    /** 工具schema和执行目标来自具名Actions；同层重名报错，局部范围可覆盖外层。 */
    tools(spec: PromptToolContribution): Disposer;
    assemble(context: ActivationContext): Promise<{
        readonly sections: readonly string[];
        readonly contexts: readonly string[];
        readonly tools: readonly Json[];
    }>;
}
export interface GraphSeed {
    readonly actors: readonly Actor[];
    readonly edges: readonly {
        readonly from: string;
        readonly to: string;
        readonly attributes: Json;
    }[];
}
export interface TopologyProvider {
    build(input: Json, context: BootstrapPreparationContext): Promise<GraphSeed>;
}
export interface SocialReadPort {
    neighbors(actorId: string): Promise<readonly string[]>;
}
export interface PropagationPolicy {
    recipients(input: {
        readonly actorId: string;
        readonly content: Json;
    }, view: SocialReadPort): Promise<readonly string[]>;
}
export interface MeasurementPolicy {
    shouldSample(frontier: Json): boolean;
}
