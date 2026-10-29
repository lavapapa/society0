import type { Actor } from '../actors/index.js';
import type { BootstrapContext } from '../initializers/index.js';
type BootstrapPreparationContext = Pick<BootstrapContext, 'runId' | 'schema' | 'initialTime' | 'signal' | 'artifacts'>;
type SqliteReader = SqliteReadView | SqliteTransaction;
/** 领域包拥有实际 SQLite 表、索引与 SQL。 */
import type { Json, Disposer, Invocation, Lease } from './common.js';
import type { ActivationContext, ActivationResult } from './runtime.js';
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
export interface ActionExecutionContext extends ActivationContext {
    readonly actionRegistrationId: string;
    /** 同一原生写事务开头复查主体及动作资格，再执行全部领域写入。 */
    transaction<T>(request: TransactionRequest, body: (tx: SqliteTransaction) => Promise<T>): Promise<{
        readonly value: T;
        readonly receipt: CommitReceipt;
    }>;
}
export interface ActionDefinition {
    readonly name: string;
    readonly inputSchema: Json;
    readonly outputSchema: Json;
    readonly effect: 'world' | 'external' | 'read';
    available(scope: DiscoveryScope, reader: SqliteReader): Promise<boolean>;
    execute(input: Json, context: ActionExecutionContext): Promise<ActionReceipt>;
}
export interface ActionReceipt {
    readonly requiredOperations?: readonly string[];
    readonly operationId: string;
    readonly status: 'accepted' | 'completed' | 'waiting' | 'incomplete' | 'rejected';
    readonly value: Json;
    readonly original: Json;
    readonly artifacts: readonly ArtifactRef[];
}
export interface ActionSelection {
 discover(scope:DiscoveryScope,view:SqliteReadView,request:PageRequest):Promise<Page<Omit<ActionDefinition,'execute'|'available'>>>;
 execute(name:string,input:Json,context:ActivationContext):Promise<ActionReceipt>;
}
export interface Actions {
    acquire(context:Invocation):Promise<Lease<ActionSelection>>;
    register(definition: ActionDefinition): Disposer;
    discover(scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<Omit<ActionDefinition, 'execute' | 'available'>>>;
    execute(name: string, input: Json, context: ActivationContext): Promise<ActionReceipt>;
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
