/** 生产契约：pi负责通用工具循环，宿主负责领域完成判定与持久化。 */
import type { Api, Model, SimpleStreamOptions } from '@earendil-works/pi-ai';
import type { AgentContext, AgentEvent, AgentLoopConfig, AgentMessage, AgentTool, PrepareRequest, PrepareRequestContext, StreamFn } from '@earendil-works/pi-agent-core';
import type { Disposer, Invocation, Lease } from './common.js';
import type { ActionReceipt } from './domain.js';
import type { SelectionLease } from '@society0/host/slots';
import { slotKey } from '@society0/host/slots';
import type { PhysicalStatus } from '../attempts/transport.js';

/** 由模型provider准备并绑定的快照；外层Lease覆盖请求、工具及持久化结算。 */
export interface ResolvedModelSelection {
  readonly provider: string;
  readonly model: Model<Api>;
  readonly registrationId: string;
  readonly contextWindow: number;
  /** 凭据由绑定的streamFn解析，配置与业务证据不携带凭据正文。 */
  readonly options: Readonly<Pick<SimpleStreamOptions, 'reasoning' | 'maxTokens' | 'temperature' | 'transport' | 'maxRetries' | 'sessionId' | 'metadata'>>;
  /** 直接连接Models.streamSimple；保持原生事件及签名，避免经过ModelEvent往返转换。 */
  readonly streamFn: StreamFn;
  readonly attemptIds?: readonly string[];
  /** 本selection已结算的物理请求；登记发生在ledger写入成功之后。 */
  readonly attemptSettlements?: readonly ModelAttemptSettlement[];
  drain?():Promise<void>;
}
/** 物理模型请求的持久结论；仅在attempt ledger写入成功后登记。非pi provider（如replay）不登记，空数组不表示没有物理请求。 */
export interface ModelAttemptSettlement {
  readonly physicalRequestId: string;
  readonly status: PhysicalStatus;
  readonly httpStatus: number | null;
  readonly cause: string | null;
}
export interface ModelSelectionRequest {
  readonly provider: string;
  readonly model: string;
  readonly reasoning?: SimpleStreamOptions['reasoning'];
  /** 线程策略给出的稳定标识；同一线程复用同一值。 */
  readonly sessionId?: string;
  /** 请求体 metadata；LiteLLM 用 session_id 做粘性路由。 */
  readonly metadata?: Readonly<Record<string, string>>;
}
export interface InferenceModels {
  prepare(request: ModelSelectionRequest, context: Invocation): Promise<Lease<ResolvedModelSelection>>;
}
export interface InferenceBudget {
  readonly maxRequests: number;
  readonly maxToolCalls: number;
  readonly maxTurns?: number;
}
export interface RequiredAction { readonly name: string; readonly minimumCompleted: number }
export interface InferenceActionReceipt {
  readonly toolCallId: string;
  readonly action: string;
  readonly receipt: ActionReceipt;
}
/** 绑定到本次activation；只返回Actions持久化完成后登记的事实，独立于工具展示details。 */
export interface ActionReceiptReader {
  read(): Promise<readonly InferenceActionReceipt[]>;
}
export interface InferenceSinkLimits {
  /** 包含正在保存的事件；达到上限停止接收、取消生产者并等待已开始工作结算。 */
  readonly maxPendingEvents: number;
  readonly maxPendingBytes: number;
}
export interface SequencedInferenceEvent {
  readonly sequence: number;
  readonly event: AgentEvent;
}
export interface UndispatchedToolReceipt {
  readonly toolCallId: string;
  readonly toolName: string;
  readonly status: 'not_dispatched';
  readonly reason: 'cancelled';
}
export type InferenceStopReason =
  | 'success' | 'context_limit' | 'output_limit' | 'budget_exhausted'
  | 'required_action_failed' | 'tool_failed' | 'cancelled' | 'provider_error'
  | 'transport_unknown' | 'persistence_unknown' | 'persistence_error'
  | 'BUDGET_OUTPUT_TOO_SMALL';
/** pi-ai 实际可用于输出的 token 低于 driver output 槽时的本地拒绝证据；未发请求。 */
export interface OutputBudgetDiagnostics {
  readonly estimatedPrompt: number;
  readonly contextWindow: number;
  readonly safety: number;
  readonly output: number;
  readonly maxTokens: number;
}
export interface InferenceOutcome {
  readonly status: 'completed' | 'incomplete';
  /** 正文被识别为工具调用的次数；只作诊断，不执行正文。 */
  readonly textualToolCall?: number;
  readonly reason: InferenceStopReason;
  readonly requests: number;
  readonly actionReceipts: readonly InferenceActionReceipt[];
  readonly undispatched: readonly UndispatchedToolReceipt[];
  readonly attemptIds: readonly string[];
  /** 与selection登记一致；unknown表示物理请求已有未知外部后果。 */
  readonly modelAttempts: readonly ModelAttemptSettlement[];
  /** 输出预算本地拒绝的诊断；仅在本地拒绝时出现。 */
  readonly outputBudget?: OutputBudgetDiagnostics;
}
export interface InferenceEventSink {
  readonly limits: InferenceSinkLimits;
  /**
   * 并发append在入队时按activation单调赋号；捕获不可变事件，按序串行保存。
   * pending事件数/编码字节包含正在保存项，超限拒绝新项并通知runtime取消生产者。
   * pi工具update可能并发进入此接口；其Promise在工具结束时统一等待，
   * 过程更新落盘不构成世界副作用前置条件；Actions必须独立await真实回执持久化。
   * 此预算不覆盖pi内部EventStream或网络缓冲，需按provider另验背压。
   */
  append(event: AgentEvent, context: Invocation): Promise<void>;
  /** 停止接收后等待所有已接收保存任务结束，传播首个保存失败。 */
  drain(): Promise<void>;
  /** 取消后补齐已声明但未派发的工具关系；保存为宿主证据并进入恢复投影。 */
  appendUndispatched(receipt: UndispatchedToolReceipt, context: Invocation): Promise<void>;
}
export interface InferenceRequest {
  readonly threadId: string;
  readonly interactionId: string;
  readonly prompts: readonly AgentMessage[];
  readonly history: readonly AgentMessage[];
  readonly tools: readonly AgentTool[];
  /** 与tools来自同一ActorToolBinding；完成策略从此端口读取真实领域回执。 */
  readonly actionReceipts: ActionReceiptReader;
  readonly selection: ResolvedModelSelection;
  readonly budget: InferenceBudget;
  /** driver output 槽；pi-ai 实际 max_tokens 低于此值时不发请求。 */
  readonly minOutputTokens?: number;
  readonly requiredActions: readonly RequiredAction[];
  /** required-first 只用于线程第一次请求；默认 auto。 */
  readonly toolChoice?: 'auto' | 'required-first';
  /** 正文工具调用纠正重试次数；默认 1。 */
  readonly textualToolCallRetries?: number;
}
/** 每次activation创建独立AgentContext；selection/extensions的lease由调用者持有至run结算。 */
export interface AgentRuntime {
  run(request: InferenceRequest, context: Invocation, sink: InferenceEventSink,
    extensions: InferenceExtensions): Promise<InferenceOutcome>;
  /** 等待本实例已开始的物理任务及持久化结束；远端结果未知保留unknown。 */
  drain(reason: string): Promise<void>;
}
export interface AgentRuntimes {
  register(name: string, runtime: AgentRuntime): Disposer;
  acquire(name: string): Promise<Lease<AgentRuntime>>;
}

/** typed策略通过现有Slots选择，不另建DI或扩展注册表。 */
export interface InferenceRequestExtension {
  /**
   * 仅pi原生context/model/thinkingLevel；next显式传递新的输入。
   * model必须仍为selection.model快照；身份或配置变化须结束当前租约并重新prepare。
   * temperature/maxTokens/transport/maxRetries固定来自selection.options，禁止共享对象偷传。
   */
  prepare(input: PrepareRequestContext, next: (input: PrepareRequestContext) => ReturnType<PrepareRequest>, signal?: AbortSignal): ReturnType<PrepareRequest>;
}
export interface InferenceContextExtension {
  /** 当前版本保留全量历史；压缩策略延后。 */
  transform(messages: AgentMessage[], context: Invocation): Promise<AgentMessage[]>;
}
export interface InferenceToolPolicy {
  before?: AgentLoopConfig['beforeToolCall'];
  after?: AgentLoopConfig['afterToolCall'];
}
export interface InferenceCompletionFacts {
  readonly context: AgentContext;
  readonly requiredActions: readonly RequiredAction[];
  readonly actionReceipts: ActionReceiptReader;
  readonly stopReason: InferenceStopReason;
  readonly requests: number;
}
export interface InferenceCompletionPolicy {
  /** 领域成功依赖completed回执；agent_end单独不足以构成成功。 */
  evaluate(facts: InferenceCompletionFacts): Awaitable<Pick<InferenceOutcome, 'status' | 'reason'>>;
  finishTurn?: AgentLoopConfig['finishTurn'];
}
export interface InferenceObserver {
  /** 非关键观察；持久化使用强制等待的sink，观察失败不得覆写领域结果。 */
  observe(event: AgentEvent, context: Invocation): Awaitable<void>;
}
export const INFERENCE_REQUEST = slotKey<InferenceRequestExtension>('society/inference-request');
export const INFERENCE_CONTEXT = slotKey<InferenceContextExtension>('society/inference-context');
export const INFERENCE_TOOL_POLICY = slotKey<InferenceToolPolicy>('society/inference-tool-policy');
export const INFERENCE_COMPLETION = slotKey<InferenceCompletionPolicy>('society/inference-completion');
export const INFERENCE_OBSERVER = slotKey<InferenceObserver>('society/inference-observer');
export interface InferenceExtensions {
  /** middleware：由Slots解析贡献顺序，runtime组合到prepareRequest。 */
  readonly request: SelectionLease<InferenceRequestExtension>;
  /** ordered：逐项转换可见投影，完整Thread始终保留。 */
  readonly context: SelectionLease<InferenceContextExtension>;
  /** ordered：before遇block停止；after按顺序字段替换，遵循pi的完整字段覆盖规则。 */
  readonly toolPolicy: SelectionLease<InferenceToolPolicy>;
  /** single：一个明确的领域完成判定。 */
  readonly completion: SelectionLease<InferenceCompletionPolicy>;
  /** ordered：非关键观察贡献。 */
  readonly observer: SelectionLease<InferenceObserver>;
}

export type Awaitable<T> = T | Promise<T>;
export interface ModelProvider {prepare(input:ModelSelectionRequest,context:Invocation):Promise<Lease<ResolvedModelSelection>>;close():Promise<void>}
export type ModelEvent = {kind:'delta';original:unknown}|{kind:'complete';message:unknown;usage:unknown;original:unknown}|{kind:'incomplete';reason:string;original:unknown};
