# 模型接口与订阅接入选型

核实日期为 **2026-10-04**。本轮将[前次提供方研究](reuse-provider-study-20261004.md)收敛为一个首选和一个必要备选：**首选 Pydantic AI slim 的低层 Model/EmbeddingModel，备选 LiteLLM SDK**。ChatGPT 订阅接入采用官方 SIWC 协议，认证由 Authlib 承担标准 OAuth/OIDC 操作，项目保留窄的注册、凭据存储和请求配置衔接。此结论是实施建议；本轮仅阅读官方资料和运行隔离 HTTP 替身，未登录、读取真实凭据、调用模型或修改产品依赖。

## 一、结论

选型需要同时满足 Python 内进程的低层调用、完整消息与工具签名回放、订阅授权来源清楚三个条件。一个框架拥有 OAuth 或工具发现功能，并不意味着它的全部代理运行时都适合接管 Society0。

### 1.1 首选与备选

Pydantic AI **2.54.0** 已发布低层 `Model.request`、`request_stream`、直接请求函数及嵌入接口。Society0 可以保留自己的领域行动、完整 Thread、步骤完成和记忆合同，由成熟库处理提供方协议。本轮实际在 Python 3.12 隔离环境安装 `pydantic-ai-slim[openai]==2.54.0`，解析并安装 26 个 distribution，包含 OpenAI 3.24.0、HTTPX2、Pydantic Graph、Logfire API 和 OpenTelemetry API。slim 仍有基础依赖，实际使用低层接口不需要创建 Agent、图执行器、网关或遥测导出器。[正式版本](https://github.com/pydantic/pydantic-ai/releases/tag/v2.54.0)、[Direct API](https://pydantic.dev/docs/ai/core-concepts/direct/)。

**LiteLLM 1.104.0 SDK 是唯一框架备选**，启用条件是 Pydantic 在实际必需提供方、原始字段或协议维护方面出现经验证的缺口。空环境 dry-run 解析 58 个 distribution，包含 boto3、tokenizers 等；此数用于说明安装面，尚未测两者启动时间或驻留。首版不引入 LiteLLM Router/Proxy、Redis、自动 fallback 或第二套工具循环。[SDK 文档](https://docs.litellm.ai/docs/)、[正式版本](https://github.com/BerriAI/litellm/releases/tag/v1.104.0)。

pi-ai **1.0.2** 的正式包名为 `@earendil-works/pi-ai`，提供独立模型和 OAuth 入口；原 `@mariozechner` 名称属于历史。其 TypeScript 协议实现值得参考，但当前 Python 主体还需要 Node 运行时、跨进程历史传输及另行核实的嵌入方案，本轮不将它列为同时维护的第三后端。正式包要求 Node ≥22.19，直接依赖多个提供方 SDK。[固定包声明](https://github.com/earendil-works/pi/blob/v1.0.2/packages/ai/package.json)。

### 1.2 首版的订阅决定

建议首版正式目标为 **`chatgpt-plan`：官方 SIWC + Pydantic Responses 低层请求**。Pydantic 的内置 `openai-codex` 路线已具技术实现，可作为独立、显式选择的兼容性研究 profile；当前不把它设为产品默认或自动 fallback。该选择给出了使用现有 ChatGPT Plus/Pro 计划的实施路径，账号是否授予 plan usage、特定模型是否可调用以及是否使用额外 credits，仍以本人授权和官方实际返回为准。

内置 Codex 技术能力与 SIWC 官方支持分开留证。本轮没有验证当前用户账户，也没有发现足以把“读取 Codex CLI 登录状态并直连内部 backend”概括为“官方已支持 Society0 按这一方式使用订阅”的依据。后续若评估该 profile，应明确授权来源、与 Codex CLI 并用的刷新所有权和模型能力限制；它不替代正式 SIWC 接入验收。

## 二、授权路线

OpenAI 已公布开源、本地应用接入 ChatGPT 计划的正式路径。现有库内置的 Codex OAuth、官方 SIWC、运行 Codex app-server 是三个不同层次，端点、凭据生命周期与执行所有权需要分别核对。

### 2.1 官方 SIWC

SIWC 使用动态注册：初次授权的 client ID 为 `dynamic_agent_client`，回调返回该用户和所选 workspace 的 issued client ID，换码及刷新使用后者。应用保留独立 host ID，请求 plan usage scopes，并在获得同意后调用公共 `api.openai.com/v1/responses`。官方面向符合资格的 Plus/Pro 用户；付费或代他人远程托管应用另有申请条件，自托管 VM 有单独流程。本轮没有把这项公开说明推断为当前账户已通过资格检查。[官方总览](https://developers.openai.com/siwc/token-sharing-open-source)、[注册](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[自托管 VM](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)。

订阅使用不等于免费 API。官方说明额度及可用 credits 参与使用管理，多个应用可能共享计划额度；应明确显示当前账号和计划，并在额度耗尽时结束或等待，保持禁止付费 fallback 的运行约束。当前用户明确禁止 DeepSeek 付费测试，本轮也没有任何真实推理请求。[会话与使用管理](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)。

SIWC preview 要求 `stream=true`、`store=false` 和每次完整输入；HTTP 不使用 `previous_response_id`。system 材料需保留为 developer 消息或 instructions，函数/custom 工具放入 namespace 或 additional_tools。temperature、输出 token 上限、metadata 及 Responses `tool_search` 等能力不受支持。这会影响 Society0 的可选调温及提供方输出上限：应在运行配置阶段明确能力冲突，保留用户决策，避免默默丢参数。行动/轮数预算仍由 Society0 按既有研究合同执行；订阅端无法表达的输出上限不应写成已保证。[Preview 合同](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)。

### 2.2 三个库现有 Codex 实现

Pydantic 2.54.0 的 `OpenAICodexModel` 继承 `OpenAIResponsesModel`，可直接调用，无需 Agent。其 `OpenAICodexProvider` 默认读取 CLI auth 文件，或者接受显式 credentials/credential_source；使用 CLI 公共 client ID 与 `https://chatgpt.com/backend-api/codex`，包含过期刷新、单实例并发刷新合并和一次 401 刷新重放。默认读取 CLI 文件后，刷新结果留在内存；长期运行宜由应用明确拥有 credential source。提供方客户端没有自动关闭 SDK 默认重试，因此接入时需显式设置单一重试所有权。[固定 model](https://github.com/pydantic/pydantic-ai/blob/v2.54.0/pydantic_ai_slim/pydantic_ai/models/openai_codex.py)、[固定 provider](https://github.com/pydantic/pydantic-ai/blob/v2.54.0/pydantic_ai_slim/pydantic_ai/providers/openai_codex.py)。

LiteLLM 的 `chatgpt/` 路由提供独立 `responses`/聊天桥接调用，已发布源码包含设备登录、token 保存和刷新。其默认同样指向 ChatGPT backend；文档明确剥离不支持的 token 上限与 metadata，非流式调用会汇集上游 SSE。此自动处理方便普通应用，但迁移本项目时需要前置配置校验与真实物理请求留证。更换 base URL 并不自动替换其 client ID、授权 scopes 和 SIWC 工具格式。[ChatGPT 路由](https://docs.litellm.ai/docs/providers/chatgpt)、[1.104.0 认证实现](https://github.com/BerriAI/litellm/blob/v1.104.0/litellm/llms/chatgpt/authenticator.py)。

pi-ai 1.0.2 也提供 Codex OAuth 与模型适配；固定源码使用相同 CLI 公共 client ID，包含授权码/设备授权和刷新。它解决一类真实协议工作，但公开代码存在不构成服务方对所有第三方应用的授权承诺，也没有自动对接 SIWC 的动态注册协议。[固定 OAuth 源码](https://github.com/earendil-works/pi/blob/v1.0.2/packages/ai/src/auth/oauth/openai-codex.ts)。

### 2.3 官方资产的复用边界

Codex app-server 的 SIWC 文档要求调用者提供 access token，并自行刷新；更新后重启进程、恢复 thread。它使用 Codex 的 thread/turn RPC 和代理执行机制。为了借用登录而引入它，并不能消除认证责任，反而增加第二套代理上下文。本次低层提供方选型不采用该路径。[官方 app-server 接法](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)。

官方 Cookbook 另提供 Sign in with ChatGPT DevKit。本次仓库主干为 `f723814abdccec135b519c451fb6e1992ee5e933`，未发现 release/tag；`@siwc/local` 标记 0.1.0、`private:true`，是 Node ≥22 的仓库 workspace。其公开 `createChatGPT` 已负责登录、账户与刷新，但 `streamResponse` 接口仅收文本消息并返回 text，未公开通用凭据访问或工具请求入口。它不是可直接替换 Society0 提供方的完整 SDK。[Cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)、[固定接口](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/f723814abdccec135b519c451fb6e1992ee5e933/packages/local/src/types.ts)。

DevKit 的 Noncommercial License 对商业用途有明确限制，范围包括为企业或客户利益进行的开发和测试。因此本项目将其作为公开行为参考，产品不复制或依赖其实现。该限制属于 DevKit 代码许可，与独立实现 SIWC 协议的资格问题分别判断。[固定许可](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/f723814abdccec135b519c451fb6e1992ee5e933/LICENSE)。

## 三、最小接法

官方协议仍需少量项目衔接。选择成熟 OAuth 库和公开模型设置，可以让这些代码集中于服务特有规则，避免再造认证算法、流解析器或代理循环。

### 3.1 认证和依赖

建议采用 **Authlib 1.8.0** 的 `AsyncOAuth2Client`，使用其 PKCE、换码、刷新和 OIDC 工具；当前版本已使用 HTTPX2，可与本次 Pydantic 依赖共存。身份签名、JWKS 与 claims 交给 Authlib/Joserfc，存储可复用平台凭据库；首轮隔离环境的 Authlib 增量为 Authlib、Joserfc、cryptography、cffi、pycparser 五个 distribution。试验曾额外安装 legacy httpx/httpcore 两包，它们未被本探针使用，正式依赖无需照抄。[Authlib 1.8 HTTP 客户端](https://docs.authlib.org/en/stable/oauth2/client/http/index.html)。

项目衔接职责是启动 loopback 回调、保存授权尝试和 issued client ID、验证服务特有的 plan scopes/账号绑定、持久化轮换结果并控制一个账号的刷新所有权。官方的 `earliest_refresh_at` 与账户选择也属于这层。Authlib 并不自动理解 OpenAI 的动态 client ID 回调约定，应用在换码前必须绑定收到的 issued ID。估计认证衔接及凭据库集成约 **150–250 行**，加上提供方 wire/profile 衔接约 **40–80 行**，这是待实现范围估计，包含在全项目维护代码中；未将其排除后声称 Core 更小。

### 3.2 模型公开 API

本轮离线验证 `OpenAIResponsesModel` + `OpenAIProvider` + 显式 SDK 客户端即可处理 SIWC 请求。公开 profile 设置强制流式、关闭远端存储、把 system role 映射为 developer；公开 `extra_body` 设置 namespace tools，返回的 namespace 通过 `ToolCallPart.provider_details` 继续回传。所用配置不依赖私有继承或请求拦截器。生产实现应从同一批实际可用 ToolDefinition 生成 namespace 内容，避免分叉两套工具权威。

SIWC bearer token 由认证组件在实际请求前提供，OpenAI SDK 与 Pydantic 负责 Responses/SSE。需要保留未知扩展字段时另核原始响应钩子；本轮通过的是工具参数、namespace 与 opaque reasoning signature，尚未证明任意未来字段都无损。若公开 API 后续无法覆盖实际 wire，局部采用现有 OpenAI SDK 发送该 profile 是可接受的小特例，不扩展成另一完整框架。

模型库不执行 Society0 领域工具，也不代替预算和成功判定。`parallel_tool_calls=false` 的正确传输与模型是否遵守是两个验收对象。SIWC 不支持的 native tool search 也不影响本地动态行动发现通过普通 function 工具表达；授权、对象绑定、当前资格和步骤故障继续由共享环境负责。

## 四、证据

隔离试验足以确认接口组合可用，并暴露内置 Codex 与新 SIWC 的格式差异。它没有连接账号，无法证明订阅资格或实际服务稳定性。

### 4.1 离线结果

[模型探针](provider-selection-offline-20261004/model_probe.py) 使用真实已发布 Pydantic/OpenAI SDK、`httpx2.MockTransport` 和合成凭据；socket connect 被明确阻断。两次内置 Codex 请求保留完整工具参数和 opaque 签名，过期凭据触发一次合成刷新。捕获的原请求包含顶层 function 和 system 消息，因此不能原样冒充 SIWC。

同一探针随后使用公共 Responses provider 配置，完成两次 namespace 工具往返；全部历史继续传入，回传 namespace 和 reasoning 签名保留，store/stream 与 HTTP continuation 配置满足本次所测要求。[实际请求工件](provider-selection-offline-20261004/model-result.json)包含合成正文和精确依赖版本，不含真实 token。这里的 mock 响应验证 SDK 映射，未证明服务器会接受整个请求或同一模型服从相同参数。

[OAuth 探针](provider-selection-offline-20261004/oauth_probe.py)验证 Authlib 生成授权请求，回调后将动态占位 client ID 换为 issued ID，换码和刷新均携带正确 resource，PKCE 由库处理。[结果](provider-selection-offline-20261004/oauth-result.json)明确标注 OIDC 身份校验和凭据持久化未测试。后续需补合成 JWKS/ID token、拒绝授权、刷新失败及持久化失败的离线消费者，再申请真实登录验证。

重跑时在临时虚拟环境安装 `pydantic-ai-slim[openai]==2.54.0` 与 `Authlib==1.8.0`，直接运行两个 Python 文件即可；它们不导入 Society0 产品，也不读取环境密钥、CLI auth 文件或启动浏览器。LiteLLM 的[依赖解析记录](provider-selection-offline-20261004/litellm-resolution.txt)仅为 dry-run，未安装或调用它。

### 4.2 尚待接受的风险

真实接受门槛包括当前账号获得明确 plan usage、使用可用模型完成完整工具往返、确认额度与 credits 行为、取消及流内失败正确传播，以及签名持久化后续接。迁移还须验证 SDK 重试关闭后每次物理请求留证、排队期间不物化 Thread、读取失败不重发工具与 Memory 嵌入数值一致。Authlib 与模型库的接口可用性已经离线证明，完整生产认证与 Society0 组合尚未实现。

## 五、其他计划

不同 coding 计划有不同的正式接入方式。OAuth 适配代码的存在，不能推出所有订阅均可作为通用推理服务。

Claude 官方区分直接第三方应用接入与运行未修改的 Claude Code。第三方产品通常应使用 API key 或受支持云提供方；原生 Claude Code 可以在官方条件下由用户自行登录。为节省推理费用而把其订阅 token 注入自有低层 provider，不纳入本次方案。[Claude 官方说明](https://code.claude.com/docs/en/legal-and-compliance)。

Gemini CLI 官方明确限制第三方软件借用 CLI OAuth 直连后端，推荐第三方应用采用 AI Studio 或 Vertex 的正式凭据。Google AI Pro/Ultra 与 Developer API 免费档分别核实；后续免费测试可评估明确免费的 Developer API 项目，避免借 CLI 登录代替资格判断。[Gemini CLI 官方条款说明](https://geminicli.com/docs/resources/tos-privacy/)。

GitHub Copilot SDK 有正式 OAuth/订阅接入文档，并提供 Python 客户端，但该 SDK 连接 Copilot CLI，由 CLI 执行代理工具循环。它适合可选的完整外部 Driver 研究，当前低层模型协议替代不因此引入另一套执行循环；组织策略、计划额度和实际计费仍需另行确认。[官方认证](https://docs.github.com/en/copilot/how-tos/copilot-sdk/auth/authenticate)、[官方代理循环](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/agent-loop)。

最终收敛为 Pydantic slim 负责模型协议、Authlib 负责标准认证、SIWC 窄适配负责服务特有合同；LiteLLM 保留为经具体缺口触发的唯一框架备选。现有订阅支持已有明确的正式实现路线，真实资格及计费边界需要本人授权后验证。产品迁移、真实服务调用和发布均未在本轮执行。
