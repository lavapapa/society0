# 模型决定与请求合同

LLMDriver 通过 Pydantic AI Agent.iter 推进模型与工具节点，在主体当前会话内使用完整 Thread 做决定，通过与规则 Driver 相同的 Information、Actions 和 Runtime 会话执行。端点适配器复用现有连接池，Thread 保存实际请求水位、请求选项、完整响应与工具结果。本页描述已经落地的循环；默认主体认知构造、自动记忆策略与完整提供方插件配置由各自模块接入。

## 一、调用

构造 `LLMDriver(provider, threads, input_builder=..., policy=LLMPolicy(...), shell_factory=None, memory=None, provider_selector=None)`，交给 `Actor`。`input_builder(session)` 返回本次需要追加的消息，可同步或异步，负责完整提供 persona、precision、reminder 与经营视图。此前消息继续存在，发送时读取整个 Thread；没有隐藏的消息窗口或自动摘要。首次激活尚无任何消息时，写入内容为空的 user 协议帧，供 SDK 开始请求；该帧保存在完整 Thread 中。调用返回 `DriverResult(status, value, reason)`，value 含 thread_id、结构结果和实际行动计数。

`provider.request_model(thread_id, options, model_messages=...)` 返回原始 SDK ModelResponse、已保存的消息序号和未完成原因。Agent 在激活入口解码完整 Thread，后续请求把已有 typed 消息列表直接交给同一个提供方入口；提供方不再物化第二份历史。Agent 退出后释放该激活历史，再进入记忆提取。历史会在本次激活的工具与服务等待期间保持驻留。独立的 `provider.request(thread_id, options)` 从同一物理入口派生 assistant 角色视图及 finish_reason，供记忆提取等消费者使用。`ModelProvider(endpoints, threads, max_attempts=2, retry_delay=0.1)` 为现有模型资源管理器的适配器；关闭时执行 `await provider.close()`。物理请求消息在一次短读内与 through 水位一起捕获，所有重试引用同一水位，SDK 获得同一完整消息列表。SDK 内置重试关闭；适配器仅重试连接、超时、限流及服务端错误。必需 Thread 写入失败直接传播，避免把已发送请求当作网络失败再次发送。上下文超限返回明确未完成原因。

## 二、决定

`LLMPolicy` 的 turns、总行动次数和逐动作次数是独立预算；默认 None 表示未在此层增设限制。动作失败尝试也计数。明确的直接调用批次在执行前检查总额度，跨轮重复 call_id 先查回执；shell 内动态动作在每次 invoke 前检查。达到硬上限直接返回 incomplete，保存已发生事实，不额外请求结束语。length 响应即使含完整外观的工具调用也不执行。SDK 请求上限显式使用 max_turns，None 保持无上限；工具均按声明顺序执行。SDK 协议错误不额外重试：未声明工具、无法解析的工具 JSON、同一响应内重复工具 ID 等以 model_protocol_error 记录未完成，领域处理器异常继续传播。动态工具参数仍由 jsonschema 验证，合法 JSON 中的参数错误作为完整工具反馈，包含 jsonschema 的字段路径、错误信息、校验项和期望值；所有动态元工具共用这一反馈。

动作查找、完整描述、执行采用 action_find、action_describe、action_invoke。默认 strict_tools=False，action_invoke.arguments、data_query.query 使用原生 JSON 对象，action_find.cursor 使用对象或 null；data_list.cursor 保留提供方的任意 JSON 值，data_query.query 内的 cursor 也原样传递；工具声明显式 strict:false。strict_tools=True 将这四类动态字段声明为 JSON 字符串（游标仍可为 null），在元工具分派边界解码一次；每种模式仅接受自己声明的类型。严格模式无法解码的行动文本继续作为失败行动尝试计数。实际 Action schema、作用域与权限校验在领域入口执行。allowed_names 与 allowed_tags 共同限定本次选择，名称与标签所需动作由 required_names、required_tags 表达；未满足时按剩余推理预算继续提醒。accepted 表示请求已受理，完成状态由 completed 表达。领域 ActionResult.terminal 或配置 completion_names、completion_tags 在成功完成后触发终止；同一 shell 后续动作因此被拒绝。共享领域处理器抛错或取消会传播给 Runtime，当前步骤失败；无可用动作、陈旧游标和查询参数错误作为工具反馈。

工具回执保存原调用、完整正文及独立的行动语义元数据。重复同一 call_id 读取既有结果，不重复业务动作，并恢复该结果的成功名称、标签、终止及事实覆盖状态。相同 ID 配不同调用被拒绝。只读结果可提供 facts 引用；重复发现提示追加在完整结果旁。写入默认清空本次事实覆盖，显式 changed=false 保留。该机制没有改变原始工具正文。

可选 `empty_retry_temperature_delta` 与 `empty_retry_temperature_max` 控制空响应后的下一次请求；delta 缺省为 None，max 缺省为 1.0。基值取本次选定提供方的公开 request_options.temperature，再由激活策略的同名字段覆盖；未声明时按 0 计算。重试温度为 min(基值 + delta, max)，保留十二位小数。空响应预算仍由 empty_retries 与总 max_turns 约束；调温不增加轮次，非空响应后的正常请求恢复原基值。实际物理重试继续使用同一次请求的选项与完整消息水位。

`repeated_read_temperature_delta` 与 `repeated_read_temperature_max` 提供另一项可选策略，缺省值同上。插件将行动声明为 read_only，并用稳定 facts 引用表示所读事实；该轮读取没有新增事实时，连续重复轮数增加，下一次请求按 min(基值 + 连续轮数 × delta, max) 调整。新事实、changed 写入及非重复轮使连续次数归零；changed=false 保留已覆盖事实。无 facts 的只读行动不触发此策略，全文仍保留在工具结果中。该接口以显式事实声明替代旧版任意返回值字符串比较；不会建立完整结果缓存或强制结束主体。

两项策略的计数均属于本次激活，请求选项副本不修改提供方或策略默认配置。Thread 分别保存 provider_empty_response_retry 与 provider_repeated_read_diversification 的计数、调整前后温度；实际物理请求选项沿既有请求事实留证。循环预算不允许下一轮时，不生成该轮调温事件。

### 2.1 SiliconFlow 工具阶段

使用 [提供方配置示例](models-contract.md#11-siliconflow-配置) 的模型服务时，下面的策略用于已验证的 Qwen 工具调用阶段。推理开关与工具选择在策略中定义，累计用量设置由提供方 profile 负责。

```python
from society0.kernel.llm import LLMPolicy

qwen_tool_policy = LLMPolicy(
    parallel_tool_calls=False,
    request_options={
        "extra_body": {"enable_thinking": False},
        "tool_choice": "auto",
    },
)
```

将该策略传给使用 `qwen_tools` 模型服务的 LLMDriver。它保留默认原生对象工具协议，轮次与行动预算由本次运行显式决定。关闭推理的设置用于这项工具阶段合同；自由分析与其他认知阶段根据自身任务另配策略。参数接受性与实际工具结果续轮证据见 [提供方核查记录](../../research/core-next/provider-acceptance-20261004/provider-contract.md)。

## 三、信息

data_list 与 data_query 保留总数及继续读取游标，data_read 默认按 UTF-8 文本读取，可指定 base64 完整读取二进制。UTF-8 分块至少四字节，继续偏移避免拆开码点。数据提供方返回自身实际 revision，当前会话不宣称跨网络等待保持数据库快照。

可选 shell_factory 接收 `(session, bound_action_ledger)`，返回 ShellSession。shell 使用同一行动账本，完整 stdout、stderr、工具回执封存为步骤产物并登记到 Thread；返回预览与引用。`driver.read_result(session, reference, offset=0, size=65536, encoding='utf-8')` 可用于 ShellSession 的 result_reader，也由 result_read 元工具调用。恢复后的旧引用仍通过 Thread 的主体关联索引定位。Bashkit 输出接口为 UTF-8 文本，精确二进制内容走 data 的 base64 通路。

shell_factory 可将 WorkspaceStore 传入 `ShellSession(..., workspace=service)`。默认 cwd 为 `/workspace`，成功或 waiting 激活在 shell 空闲且尚未关闭时自动保存文件增量和 shell 状态；保存失败将 Thread 标记为 incomplete 并传播，Runtime 因而拒绝完成该步骤。主体工作区与 Thread 身份分别持久化，新的 Moment 仍可读取前一步工作区。`/tmp` 等临时目录不进入持久工作区。

同主体、同 Moment、同 mode 再激活使用持久化索引定位原 Thread，保留 provider_session_id。completed 或 waiting 可继续；incomplete 的诊断 Thread 要求显式恢复，避免换一次激活重置失败预算。恢复边界仍由完整步骤决定，未完成步骤内外部服务已经发生的事实保留在原运行诊断中。

## 四、测量

interview 模式默认提供测量工具 submit_result，决策动作不自动进入测量工具集合。result_schema 校验结构化结果；direct_json 显式启用提供方 JSON schema 响应模式。strict_tools 使用已有严格 schema 规范化能力，不在失败后静默放宽。完整 Thread、原文响应、工具参数和输出仍保存。

确定性验收覆盖自然结束、终止动作、受理与拒绝、required 多轮纠正、精确名称与标签、独立预算、重复调用、并行工具合同、结构化测量、上下文超限、截断、取消、领域异常、完整长历史、物理重试水位、恢复回执与产物读取。实际服务的参数支持、价格、吞吐和记忆闭环需由端点配置及真实服务验收另行确认。

## 五、选择

`ModelResolver(profiles, default, actor_models=..., type_models=...)` 持有已安装的具名提供方引用。`resolve(actor_id, actor_type=None, override=None)` 的优先级是显式本次覆盖、主体配置、类型配置、默认配置；未知 profile 明确报错。构造时核对静态配置引用，调用时核对本次覆盖。选择不会创建额外管理器，连接池与资源额度仍由原 profile 负责。

`LLMDriver.provider_selector(session)` 在每次激活开始调用一次，可同步或异步；返回本次完整激活使用的提供方。调用方可以写成 `lambda session: resolver.resolve(session.actor.id, actor_type=..., override=...)`，把类型来源与单次覆盖来源保留在研究配置的可见位置。同 Moment 选择另一提供方仍复用原 Thread 与 provider_session_id，完整历史继续传递；提供方物理消息水位规则不变。

`LLMPolicy(reasoning_stages=({'name':'观察','desc':'检查当前完整材料'}, ...))` 给出可选的认知阶段指导。指导正文变化时追加到 Thread，同一正文在原会话中复用。它沿用旧循环的阶段名称与描述语义，阶段之间没有额外的强制模型请求或调度屏障。结构化提交模式按其 schema 输出合同工作。

模型自愿输出 `-> stage_begin: 阶段名` 行时，返回值 reasoning_stages 按响应 message_seq 列出有序 segments，分别包含 name、known 与原始消息 content 的字符范围 start/end（左闭右开）。重复和未知阶段按原顺序保留；未识别标记的内容进入 default 段。消费者沿 thread_id/message_seq 取得原文，再按字符范围解释分段。原始完整消息继续保存在 Thread，解析结果不触发纠正请求，也不减少原文。各阶段对应模型提供的文字分段；它们不代表可以观测模型内部未公开的推理过程。


记忆 hooks 遵循激活作用域协议：`activation(session, thread_id)` 返回异步 context manager，Driver 在确定 Thread kind 后进入，退出时统一清理；before_activation 与 after_activation 在该作用域内执行。标准 Memory 的策略选择器每次解析一次，固定自动召回、写入、主动工具及 recall_top_k，详细合同见 memory-contract.md。自定义 hook 也须显式提供其作用域，避免跨激活共享可变策略。

提供方会话传输由 ModelProvider 的 `session_transport` 显式配置。默认 `None` 将稳定 `provider_session_id` 保存于 Thread 和请求记录；支持请求体 metadata 的提供方选择 `metadata`，适配器经 SDK 的 extra_body 将同一身份写入实际 JSON 请求体的 metadata.session_id，并保留其他显式 metadata。profile 中的选择保持固定，服务拒绝参数时按真实失败留证。Gemini OpenAI 兼容接口使用默认 None。

行动发现的 `query` 按行动名称与描述做忽略大小写的字面子串匹配。传入空字符串可分页枚举全部可用行动；`*` 按普通字符处理，查询不提供通配或语义匹配。工具参数与描述同时向模型说明这项规则。`parallel_tool_calls=False` 时，每个工具说明要求每次模型响应至多一个工具调用；开启并行调用的策略不添加此限制。

资料发现的工具说明区分目录、结构数据与具体原文。目录项的 `kind="directory"` 提示继续列举，结构数据集可用 `data_query` 查询；具体文件或 `document_ref` 的路径进入 `data_read`，正文引用的 `expected_revision` 在连续读取中原样传递。工具说明不包含领域路径、行动名称或预设读取顺序。
