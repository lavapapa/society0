/** 生产契约：复用pi认证协议；机器凭据与世界SQLite资料分开保存。 */
import type { AuthInteraction, CredentialInfo, CredentialStore } from '@earendil-works/pi-ai';
import type { Invocation, Lease } from './common.js';
import type { ModelProvider } from './inference.js';
export interface ModelCredentials {
  /** modify的互斥范围包含refresh请求；引用由明确配置提供。 */
  acquire(reference: string): Promise<Lease<CredentialStore>>;
}
export interface SubscriptionModelProvider extends ModelProvider {
  /** 操作者显式发起，调用pi Models.login；无需重写OAuth。 */
  authorize(interaction: AuthInteraction, context: Invocation): Promise<CredentialInfo>;
}
