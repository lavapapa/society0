# 多提供方接口的成熟实现评估

核实日期为 **2026-10-04**。本轮阅读 Society0 `0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0` 的请求路径，以及候选项目的官方文档、源码和发布元数据；没有安装候选、读取密钥或发出模型请求。建议优先验证 **Pydantic AI 的低层 Model/direct 与 EmbeddingModel**，以 LiteLLM SDK 作为覆盖范围更广的对照；pi-ai 适合另作跨语言方案评估，aisuite 当前有原文与 thinking 流保留方面的明确缺口。这里提出的是替代试验顺序，尚未形成已验证的迁移决定。

## 一、边界

现有模块同时承担协议翻译、资源调度和模拟语义。成熟库能减少协议维护，但替换时必须沿实际职责切开，否则容易留下两套重试、两套工具执行和重复的全文消息副本。

### 1.1 当前职责

[`resource_managers.py`](../../src/society0/resource_managers.py) 中，`LLMManager._add_endpoint` 建立 OpenAI/Azure 客户端与连接池，`_execute_request` 处理许可、物理请求、计时和响应留证，`_convert_response` 翻译响应；嵌入管理器另负责 OpenAI/Ollama 分支、动态合批、拆批、缓存和每段文本结果分发。并发许可约束在途请求，尚不等同于服务的 RPM、TPM 或日配额。

[`kernel/models.py`](../../src/society0/kernel/models.py) 将这些请求连接到 Thread 和 ResourceCalls：固定消息水位，取得许可后才物化完整请求，区别传输失败与留证失败，保存每次物理调用及逻辑引用，提供受控取消与关闭。`ModelProvider` 选择端点后重试同一输入，SDK 自身重试被关闭；这层目前继承旧 manager，使协议和可恢复留证耦合较深。

[`kernel/llm.py`](../../src/society0/kernel/llm.py) 的核心是社会模拟驱动合同：完整 Thread、同 Moment 续接、受理与完成、领域行动预算、必需行动、成功终止、步骤故障、记忆三开关、工作区、重复事实和可选调温。这些语义有真实消费者，不能凭引入通用 Agent 自动执行循环就视为对等。模型协议库适合接替单次请求与响应转换，领域行动仍由 Society0 的绑定门面执行。

### 1.2 经典问题

提供方 schema 方言、工具参数增量、thinking 签名、结束原因、SDK 扩展字段与错误映射，属于持续变化的协议问题。现有 OpenAI 兼容入口已保留工具调用中的 opaque 字段，但这种局部保留不代表完整覆盖 Anthropic 原生 content blocks、Google 原生 thought signatures 或 Responses 的所有内容类型。成熟适配库的主要价值是维护这些跨协议差异。

调用取消、连接超时、服务重试、流中断和连接释放属于传输生命周期；每次实际请求的可追溯性属于运行合同。自动 fallback 会改变实际模型或部署，自动截断/压缩会改变主体输入，自动工具执行可能改变领域顺序。因此首个替代试验应采用单 profile、单次低层请求，显式关闭额外 fallback、隐藏重试与自动循环，再决定成熟组件能接走哪些调度职责。

## 二、候选

维护状态用官方 release 与源码身份核对，避免把博客中的旧包名、主干能力和已发布版本混在一起。以下版本是本次读取时的结果，正式验证仍需固定具体包和依赖。

### 2.1 Pydantic AI：首选低层验证

官方最新 release 为 [v2.54.0](https://github.com/pydantic/pydantic-ai/releases/tag/v2.54.0)，发布于 2026-10-03；本次主干提交为 `c68786e9bc525574daa9914b23de05daf9ca4ca0`，同日仍有维护。项目为 Python，官方提供 OpenAI、Anthropic、Google、Bedrock、Groq、Ollama 等模型或兼容提供方。安装可选择 slim 与需要的 provider extras，实际依赖重量须在干净环境测量。[官方模型目录](https://ai.pydantic.dev/models/overview/)、[安装文档](https://ai.pydantic.dev/install/)。

`direct.model_request` 与 `model_request_stream` 是 Model 实现的薄入口，允许自有调用循环控制工具执行，适合当前 Society0。它使用类型化 ModelMessage/ModelResponse，迁移需明确映射 system、tool、thinking 与 finish reason；特别是 direct API 的 instructions 使用最新一份，不能将本项目全部累积 system 消息机械转换为这个字段。[Direct Model Requests](https://ai.pydantic.dev/direct/)。

消息模型显式保存 `ThinkingPart.signature`、`provider_name` 和 `provider_details`，覆盖 Anthropic/Bedrock 签名、Google thought_signature、OpenAI encrypted_content 的对应语义。工具参数支持部分增量合并。类型化响应仍不同于完整 HTTP 原始响应，不能据此声称所有未知字段都已保全；首验需同时验证未识别扩展字段、工具签名回放和必要原始响应取证入口。[消息 API](https://ai.pydantic.dev/api/messages/)、[Model API](https://ai.pydantic.dev/api/models/base/)。

正式 EmbeddingModel/Embedder 已存在，v2.54.0 源码包含 OpenAI、Google、Cohere、Bedrock、VoyageAI 和 Sentence Transformers 路径；OpenAI 兼容提供方还覆盖 Azure/Ollama 等。它区分 query 与 documents 的嵌入用途，这必须按现有 profile 显式映射，不能悄悄添加任务前缀或改变维度/向量。未找到可直接替代本项目跨主体动态微批和逐逻辑来源回执的同等合同。[嵌入文档](https://ai.pydantic.dev/embeddings/)、[固定版本嵌入实现](https://github.com/pydantic/pydantic-ai/blob/v2.54.0/pydantic_ai_slim/pydantic_ai/embeddings/__init__.py)。

超时文档区分一次请求 attempt 与整个 run；传输重试可使用成熟 HTTP 客户端，SDK 重试仍需单独核对。官方还有模型并发包装器，但其许可包住类型化消息请求，未证明能满足本项目“先排队、获准后才从 Thread 物化历史”的内存合同。取消后原生客户端是否及时关闭连接、退避期间是否释放消息，也须用实际消费者验证。[超时](https://pydantic.dev/docs/ai/core-concepts/timeouts/)、[重试](https://ai.pydantic.dev/retries/)、[并发 API](https://ai.pydantic.dev/api/concurrency/)。

建议先以 Model/direct 取代自有协议分支，不引入 Agent、Gateway 或备用模型。主要迁移风险是消息表示转换、provider_details 的持久化、SDK 重试叠加，以及新版本 HTTP 客户端类型与现有 httpx 组合。这个候选的优势是 Python 内进程、聊天与嵌入都具正式低层入口，单 profile 场景无需增加 Router 服务。

### 2.2 LiteLLM：广覆盖对照

官方最新 release 为 [v1.104.0](https://github.com/BerriAI/litellm/releases/tag/v1.104.0)，发布于 2026-10-03；本次主干 `29363076619795084143b42205db04c6b3a77932` 更新于 10-04。Python SDK 宣称统一 100 多个模型提供方，`acompletion` 保持 OpenAI 形状，迁移现有字典消息较直接；Gateway 是另一种部署选择。[官方入口](https://docs.litellm.ai/docs/)。

异步流返回增量 chunk，工具参数仍需正确完成组装；官方提供完整流重建工具。其重复 chunk 检测可能将流判错并触发重试，需核对与本项目保留原始响应和“不因重复读强制结束”的区别。[流式文档](https://docs.litellm.ai/docs/completion/stream)。thinking 使用 reasoning_content、thinking_blocks 与 provider_specific_fields 等形状，文档示例保存签名；是否完整透传某提供方的新增字段需固定版本回放验证。[Thinking 文档](https://docs.litellm.ai/docs/reasoning_content)。

嵌入 API 支持批量文本及多个提供方，可替代物理 embedding 分支；动态跨主体合批、相同文本缓存的逻辑来源分发仍需本项目消费者验证。[Embedding API](https://docs.litellm.ai/docs/embedding/supported_embedding)。SDK 支持 num_retries，Router 增加多部署负载分配、冷却、fallback 和 RPM/TPM 管理，分布式计数可使用 Redis。[重试](https://docs.litellm.ai/docs/completion/reliable_completions)、[Router](https://docs.litellm.ai/docs/routing)。

建议作为 Pydantic 低层的并列小验证候选，优先 SDK 单模型入口。若确有多部署配额、负载选择和集中管理需求，再评估 Router 接替 `_select_endpoint` 及对应限流代码。它的覆盖面优势伴随更多隐式配置、日志/缓存及重试路径；需确认未启用自动模型切换、全文日志复制或基于内容指纹的缓存，并证明 asyncio 取消、超时和流关闭可正确释放许可。官方功能清单不足以证明这些组合边界已满足 Society0。

### 2.3 pi-ai：协议能力强，跨语言成本明确

用户提到的 `@mariozechner/pi-ai` 与 `badlogic/pi-mono` 是历史入口。本次 GitHub API 将仓库解析为 `earendil-works/pi`，正式 [v1.0.2](https://github.com/earendil-works/pi/releases/tag/v1.0.2) 的 package.json 名称为 `@earendil-works/pi-ai`，发布于 2026-10-04；本次主干提交 `200387122ca450d6387f033949423114a270b96c`。本次下载的 v1.0.2 README 与主干 README 逐字相同，版本核对未使用哈希。[固定版本包声明](https://github.com/earendil-works/pi/blob/v1.0.2/packages/ai/package.json)。

这是 TypeScript 模型协议层，独立于 pi 的上层 agent/coding agent。Provider collection 支持主要原生协议和 OpenAI 兼容服务；stream/complete 提供 text、thinking、toolcall 增量及部分 JSON。调用者可直接管理上下文和执行行动。[v1.0.2 README](https://raw.githubusercontent.com/badlogic/pi-mono/v1.0.2/packages/ai/README.md)。

类型中显式保留 textSignature、thinkingSignature、Google thoughtSignature，并提供 AbortSignal、timeoutMs、maxRetries、maxRetryDelayMs、onPayload 和部分适配器的原 provider stream event 回调。onResponse 主要提供状态/头，不能当作完整原响应；流事件回调的覆盖由适配器明确决定。超时和 SDK 重试选项也带提供方支持条件。本次公开类型与目录未见统一 embedding 操作，因此尚不能据此删除嵌入适配层。[v1.0.2 类型合同](https://raw.githubusercontent.com/badlogic/pi-mono/v1.0.2/packages/ai/src/types.ts)。

推荐作为协议行为和签名回放的强参考，以及语言方案允许时的实测对照。若用于 Python Society0，需测长期 Node 工作进程、取消传播、完整历史传输、持久原文和背压；每主体一个 Node 进程不合适。跨提供方上下文转换也可能改变 thinking 表示，研究合同应固定实际模型。其 Node/TypeScript 进程成本与 embedding 缺口使它暂居 Python 低层候选之后。

### 2.4 aisuite：暂不优先

官方最近 release 为 [v0.1.3](https://github.com/andrewyng/aisuite/releases/tag/v0.1.3)，发布于 2026-07-20；主干提交 `66c7890c2d68c2d67f41723bc848bb6713d82a9c` 更新于 09-18，pyproject 标 0.2.0。主干能力不能直接等同已发布版本。它是 Python 包，提供 OpenAI、Anthropic、Google、Ollama 等适配，并有同步/异步聊天及流式工具参数。[官方 README](https://github.com/andrewyng/aisuite)、[主干包声明](https://github.com/andrewyng/aisuite/blob/66c7890c2d68c2d67f41723bc848bb6713d82a9c/pyproject.toml)。

本次源码核查有具体风险：Anthropic 流转换明确忽略 thinking deltas，client 的 `_extract_thinking_content` 会拆取 `<think>` 段并改写原 content。其简洁归一化接口不足以满足当前原文与 opaque 签名回传要求。[Anthropic 源码](https://github.com/andrewyng/aisuite/blob/66c7890c2d68c2d67f41723bc848bb6713d82a9c/aisuite/providers/anthropic_provider.py)、[client 源码](https://github.com/andrewyng/aisuite/blob/66c7890c2d68c2d67f41723bc848bb6713d82a9c/aisuite/client.py)。

本次未确认统一 embedding、运行级 RPM/TPM 或所有异步路径取消的成熟合同。若采用它，只适合先验基础 chat 适配；`max_turns` 自动工具循环应省略，保持领域行动由现有驱动执行。要为上述缺口补许多本地转换会抵消删除自有代码的收益，因此本轮不把它列为首验对象。

## 三、替代

替代价值以可删除的代码和保留的研究合同衡量。单次请求的成熟适配先行，能避免同时重写模型协议与社会语义造成失败难以归因。

### 3.1 可删除范围

Pydantic 或 LiteLLM 的低层试验通过后，首先可删除 `LLMManager._add_endpoint` 的提供方客户端分支、`_convert_response` 的手工归一化，以及 `_execute_request` 中相应 SDK 调用/usage/结束原因转换；EmbeddingManager 的 OpenAI/Ollama 物理调用与尺寸选项转换可随后由统一 embedding 接口接替。`kernel/models.py` 的继承式 `_Manager/_EmbeddingManager` 可缩为组合式薄适配，减少“新消费者覆写旧日志钩子”的结构。

Thread 水位与实际请求关联、ResourceCalls 的逻辑来源、留证失败不重发、关闭时收束任务、领域预算与完整步骤规则仍需保留为明确合同。动态微批/缓存也应单独判断：候选提供一次 batch embedding 不意味着已经管理跨主体队列、每段原文对应结果和取消。若以后成熟路由/合批组件通过同等测试，届时删除相应自有调度；此时不再另造一套通用 provider 框架。

### 3.2 关键门槛

第一组验证应在无网络的真实 SDK/HTTP 传输替身上，回放 OpenAI Chat、Responses、Anthropic 和 Google：完整多轮消息、Unicode 与大整数工具参数、分片 JSON、未知工具扩展和 thinking 签名都要持久化后再发送，核对实际 wire payload。原始响应与归一化结果分清，流未结束的部分参数不执行领域动作，length、cancel、error 与正常结束保持区别。

第二组覆盖 429/503/timeout、流中断、取消、留证失败和多实例关闭。先固定一层重试所有权，证明每次物理请求都有独立事实、重试输入水位相同、排队取消零物理请求、异常不会重放已执行行动。大量主体等待时完整消息应在实际获准发送附近物化，记录 Python/原生驻留与事件循环空窗，不能用库代码行更少推断运行更轻。

第三组嵌入验证批量原文顺序、重复文本各自来源、维度、归一化与有效数值不变，取消与部分批次失败不串主体；再接当前 Memory 与恢复消费者。第四组才是免费真实服务的工具往返和签名续接。每个候选通过的协议范围单列，未实测的提供方保持未知。

`parallel_tool_calls=False` 是请求意图与部分提供方的约束能力。库可以正确映射参数，具体模型仍可能返回多调用或不支持该字段；驱动必须继续按明确策略处理响应。工具 schema 转换、严格参数与模型行为同样分层，不能将“统一多 provider”写成“模型必然遵守行动合同”。

## 四、免费验证

本轮遵守不发模型请求的研究边界。后续验证仅使用确认免费资源，**DeepSeek 付费调用排除**；试用余额、现存密钥、付费订阅或兼容网关均不自动证明免费，也不设置收费 fallback。

### 4.1 官方免费路线

Gemini Developer API 的官方价格页为部分文本模型和嵌入模型列出 Free Tier；本次页上 `gemini-embedding-2` 的标准文本输入为免费档。模型与地区、项目档位及当前额度仍需在执行前核实，免费档与付费档同一产品名下并存。只选择页面明确列免费且运行项目确属免费档的精确模型，额外付费 grounding、缓存等功能不进入试验；达到 RPM/TPM/RPD 后等待额度或结束，不切收费服务。[官方价格](https://ai.google.dev/gemini-api/docs/pricing)、[官方限额](https://ai.google.dev/gemini-api/docs/rate-limits)。

Groq 官方列出 Free Plan 与组织级 RPM/RPD/TPM 等限额，可在确认免费账户及具体工具模型后作为第二种提供方。页面存在免费计划不证明当前账户或指定模型已可用，本轮未读取账户信息。[官方 Free Plan 限额](https://console.groq.com/docs/rate-limits)。本地 Ollama 的 local-only 模式提供另一条无远端 API 计费路线，可验证工具协议和本地 embedding，但本机算力、电力与模型许可仍有成本，也不代表云端签名协议已验。[Ollama 官方 FAQ](https://docs.ollama.com/faq)。

### 4.2 有限执行计划

先完成上述离线协议和故障回放，再对一个确认免费的工具模型做两次逻辑请求的“工具调用—回执—续接”闭环；原参数、完整历史和真实签名保留。可另做一个免费嵌入批次验证不同原文及维度；需要重复性时独立重跑同一小样，次数事前明确。失败按参数、协议、限额和模型行为分类，免费额度不足时保留结果并等待，避免长套件重试掩盖真实原因。

当前优先级是 Pydantic AI 低层模型与嵌入的小验证，LiteLLM SDK 同输入对照，pi-ai 的签名与取消协议作为跨语言备选。迁移接受条件是减少自有协议维护且保住完整输入、原始响应、调用身份、预算和恢复语义；性能、免费服务可用性与具体模型服从性分别形成证据。研究阶段到此为止，产品和依赖保持原状。
