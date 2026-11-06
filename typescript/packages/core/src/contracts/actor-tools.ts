/** 生产契约：把公开行动与探索服务绑定为pi工具，执行仍经过Actions。 */
import type { AgentTool } from '@earendil-works/pi-agent-core';
import type { Lease } from './common.js';
import type { ActivationContext } from './runtime.js';
import type { ActionReceiptReader } from './inference.js';
import type { OfferFoldState, OfferLimits } from './affordance.js';
export interface ActorToolBinding {
  readonly tools: readonly AgentTool[];
  /**
   * 绑定到本次激活的持久回执reader，独立于模型可见content/details。
   * 工具须await Actions.execute及回执保存，再登记toolCallId/action/operationId/status关联；
   * act 记录真实 action 名称。afterToolCall仅改展示，不改此事实。
   */
  readonly actionReceipts: ActionReceiptReader;
}
export interface ActorTools {
  /** 保留主体范围、动作注册身份、探索会话及回执reader，runtime和持久化结算后释放。 */
  bind(context: ActivationContext, offers?: OfferFoldState, limits?: OfferLimits): Promise<Lease<ActorToolBinding>>;
}
