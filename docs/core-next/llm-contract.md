# 模型决定与请求合同

LLMDriver 在主体当前会话内使用完整 Thread 做决定，通过与规则 Driver 相同的 Information、Actions 和 Runtime 会话执行。端点适配器复用现有连接池，Thread 保存实际请求水位、请求选项、完整响应与工具结果。本页描述已经落地的循环；默认主体认知构造、自动记忆策略与完整提供方插件配置由各自模块接入。

## 一、调用

构造 `LLMDriver(provider, threads, input_builder=..., policy=LLMPolicy(...), shell_factory=None, memory=None, provider_selector=None)`，交给 `Actor`。`input_builder(session)` 返回本次需要追加的消息，可同步或异步，负责完整提供 persona、precision、reminder 与经营视图。此前消息继续存在，发送时读取整个 Thread；没有隐藏的消息窗口或自动摘要。调用返回 `DriverResult(status, value, reason)`，value 含 thread_id、结构结果和实际行动计数。

`provider.request(thread_id, options)` 返回 assistant 消息及 finish_reason。`ModelProvider(endpoints, threads, max_attempts=2, retry_delay=0.1)` 为现有模型资源管理器的适配器；关闭时执行 `await provider.close()`。物理请求消息在一次短读内与 through 水位一起捕获，所有重试引用同一水位，SDK 获得同一完整消息列表。SDK 内置重试关闭；适配器仅重试连接、超时、限流及服务端错误。必需 Thread 写入失败直接传播，避免把已发送请求当作网络失败再次发送。上下文超限返回明确未完成原因。

## 二、决定

`LLMPolicy` 的 turns、总行动次数和逐动作次数是独立预算；默认 None 表示未在此层增设限制。动作失败尝试也计数。明确的直接调用批次在执行前检查总额度，重复 call_id 先去重；shell 内动态动作在每次 invoke 前检查。达到硬上限直接返回 incomplete，保存已发生事实，不额外请求结束语。length 响应即使含完整外观的工具调用也不执行。

动作查找、完整描述、执行采用 action_find、action_describe、action_invoke。allowed_names 与 allowed_tags 共同限定本次选择，名称与标签所需动作由 required_names、required_tags 表达；未满足时按剩余推理预算继续提醒。accepted 表示请求已受理，完成状态由 completed 表达。领域 ActionResult.terminal 或配置 completion_names、completion_tags 在成功完成后触发终止；同一 shell 后续动作因此被拒绝。共享领域处理器抛错或取消会传播给 Runtime，当前步骤失败；无可用动作、陈旧游标和查询参数错误作为工具反馈。

工具回执保存原调用、完整正文及独立的行动语义元数据。重复同一 call_id 读取既有结果，不重复业务动作，并恢复该结果的成功名称、标签、终止及事实覆盖状态。相同 ID 配不同调用被拒绝。只读结果可提供 facts 引用；重复发现提示追加在完整结果旁。写入默认清空本次事实覆盖，显式 changed=false 保留。该机制没有改变原始工具正文。

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
