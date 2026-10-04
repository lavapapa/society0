# Agent 运行时复用审查

本次以 `codex/society0-core-next` 工作树、HEAD `0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0` 加当前未提交修改为审查对象，安装并锁定的 Pydantic AI 为 2.54.0。审查期间没有读取密钥或发送真实模型请求。本次先完成离线审查与公开 API 原型，再落实 Agent 主循环和缓存用量投影，最终状态见第五部分。

## 一、判断

**本轮已采用公开 `Agent.iter` 承接主循环中的工具编排，继续使用 Model/EmbeddingModel 和 typed messages；业务账本、Thread 和完整步骤由 Society0 维护。** 原型已经证明关键业务边界可以通过公开节点 API 和普通 Tool 包装实现，无须操纵 SDK 私有计数。此前单凭“仿真有自定义需求”排除 Agent 的理由不充分。

采用前 `llm.py` 共 618 行，模型/工具循环集中在 445–589 行，共 145 行；完整 `run` 从 393 行开始，还包括输入、记忆、shell 和关闭。`_Ledger` 约 142 行，负责实际领域行动、预算和完成事实。不能将整个 Runtime、ThreadStore 或全部 llm.py 计为重复实现的通用 Agent 算法。采用前循环直接手写请求推进、工具解析、参数验证、结果分派及纠正反馈；其中部分可交给 Agent，验证本身已经复用 jsonschema。

公开 API 原型核心为 `iter_prototype.py` 的 `run`，约 95 行，使用现有 `_Ledger`、`_dispatch` 和 `_tools`，由 Agent 执行函数工具。生产接线还需原始 typed response 桥、完整 Thread 同步及当前 empty/parallel/temperature/shell/structured 分支，预计增加 40–70 行。这是范围估算，尚无最终 diff 可证明净减行。采用价值主要是让 SDK 管理通用节点与工具执行，领域适配总量预计与旧循环相近。

## 二、职责

下面按实际责任确定拥有者，避免为了名义采用 Agent 再复制一套同等规模状态机。

| 当前职责与位置 | 复用 API | Society0 适配及成本 |
|---|---|---|
| llm.py 445–589 请求、工具分派 | `Agent.iter`、`AgentRun.next`、`Tool.from_schema`，所有领域工具 `sequential=True` | 小型节点接受条件与 Tool 包装；原型已运行 |
| llm.py 433、489、525 参数/结果校验 | 有类型工具用 Pydantic；动态模型可保留 jsonschema；结构化提取可用 `ToolOutput` / `NativeOutput` | `Tool.from_schema` 不自动验证 JSON schema，需要现有成熟 validator。业务 Action 的二次资格和参数检查继续保留 |
| required_names/tags、terminal、action budget | `ModelRequestNode(ModelRequest(...))` 追加正常反馈；`UsageLimits(request_limit=policy.max_turns)` 控制请求数 | Ledger 继续按已完成动作判断；SDK 的 tool_calls_limit 统计成功工具调用，与领域行动尝试不同，不能直接替代 |
| tool_call_id 回执与作用至多一次 | Tool wrapper 中查当前 Thread 回执 | 现有 ThreadStore 与 Ledger 直接复用；跨响应重复 ID 的 SDK 默认行为会重做工具 |
| models.py 332–402 端点资源、物理请求、流与故障 | 现有 Model/EmbeddingModel 继续；提供一个公开 `Model` 接入 Agent | 应将原始 SDK ModelResponse 直接交给 Agent，保留一次实际流、一次计量，避免从 display 字典反向重建丢签名 |
| model_messages.py、ThreadStore 全历史与恢复 | `ModelMessagesTypeAdapter`、`message_history`、`conversation_id` | SDK typed requests/responses 与完整 Thread 保持一致；追加增量，避免每个节点重新复制全历史 |
| waiting、仿真时间、完整步骤 | `DeferredToolRequests` / `DeferredToolResults` 可表达外部工具等待 | accepted 与 waiting 仍由领域意义决定。完整步骤发布、任务取消、记忆和状态恢复继续由 Runtime 管理 |
| 用量与重试 | `RunUsage`、`UsageLimits`、`ModelRetry` | RunUsage 是逻辑 Agent 统计；物理尝试含网络重试仍由现有规范投影记录。SDK 类型纠正不能暗加预算，领域失败不得自动重做 |

官方 [Agent 文档](https://pydantic.dev/docs/ai/core-concepts/agent/)提供节点迭代、用量限制和结果机制，[工具文档](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/)说明函数工具与终止策略，[消息历史](https://pydantic.dev/docs/ai/core-concepts/message-history/)提供完整历史序列化，[deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)提供跨调用暂停和恢复。实际行为以本机 2.54.0 源码和探针为准。

## 三、证据

`probe.py` 使用 FunctionModel，覆盖工具类型纠正、动态 schema、截断、重复 ID、终止节点、必需动作、deferred 历史序列化恢复和结构化输出，结果见 `results.json`。SDK 对非法类型生成纠正信息，随后正确工具只执行一次；结构化结果由 -1 修正为 8；deferred 结果经过 JSON 序列化再恢复，前一阶段工具没有重执行。不同 Agent run 可以共享 conversation_id 和完整 message history。

试验发现 SDK 会将 `finish_reason=length` 的非空文本作为成功输出，所以必须在 CallToolsNode 前检查规范响应终态；公开 `iter` 已证明能在工具前拦截该响应。默认 UsageLimits 的请求上限为 50，生产必须显式传入原 policy 上限或 None。Tool.from_schema 接收 amount="invalid" 后仍执行包装函数，所以动态 schema 需要成熟 validator。SDK 拒绝同响应重复 ID，跨响应同 ID 的动作会再次执行，领域回执仍需保留。终止工具执行后，其 ToolReturnPart 暂存在下个 ModelRequestNode.request，all_messages 尚未包含它；退出前必须落盘回执。

`iter_prototype.py` 使用项目既有 setup、FakeProvider、ThreadStore 和真实 `_Ledger`。六个断言场景通过：终止动作一请求一副作用、两次提前结束提示后成功动作且无额外重试限额、跨轮重复 ID 只执行一次、批次超过预算时零副作用、length 时零副作用、同响应两个动作在首个终止后跳过后续动作。使用 `ModelRequestNode` 发送正常纠正消息，可以保留 required 业务约束而无需调整 SDK 的内部 retry 计数。

核心事实约束是完整原文、领域作用至多一次、预算没有静默缩小、失败步骤不提交、accepted/completed/waiting 区分、共享状态动作顺序。旧实现对同响应重复 ID 的合并、未知工具无限纠正、每种错误提示的文本和消息形状属于实现选择，可以通过明确协议规则重定。主循环原型采用 retries=0，将 SDK 协议错误作为显式失败；产品已经采用该明确协议并更新对应消费者，与旧纠正行为的区别保留在此。

原型仅覆盖六个场景；产品阶段又加入生产 ModelProvider 原始 typed response 桥，并运行 shell、推理阶段、温度、取消、长历史和恢复消费者。真实服务与长期性能另行验收。

## 四、实施

实施路径先让 ModelProvider 的单一流式请求入口返回原始 ModelResponse，现有语义视图由同一个结果派生；再用 Agent.iter 驱动固定元工具，Tool 包装访问现有 Ledger 和回执；最后统一 Thread 对 SDK request/response 增量的存储和展示。禁止新建 HTTP、重试、计量或第二套持久运行器。待具体 diff 收敛后再决定是否能删除旧循环全部对应分支。

失败先行验收应覆盖 length/EOF/incomplete 的零动作、业务异常零重试、取消后未完成步骤、重复 ID 的既定协议、批次预算零部分执行、顺序终止后回执、无默认50上限、同 Moment 重激活，以及完整检查点在新进程恢复后继续。缓存相关验收要对比同 Thread 多轮和恢复后的 system、tools、typed history 前缀，确认 Agent 未自动追加 instructions、重排 schema 或删除原文。真实 Qwen 工具与记忆链在冻结后独立执行。

其余重复热点中，明确优先级最高的是记忆提取：`ThreadMemoryExtractor` 617–644 行自有两次工具提取循环，`memory_extraction_protocol.py` 60 行起手工解析与校验。既有两次请求可映射 Agent 的结构化结果和 retries=1，但必须先验证真实 Qwen 及原有修复能力，当前未更换。其次是 strict schema 规范化：function_registry.py 共 172 行，与 SDK `OpenAIJsonSchemaTransformer` 部分重叠；项目把可选字段变为 nullable 并恢复默认值，SDK 仅强制 required 的变换并不天然等价，宜进一步用同 schema 做差异试验后删减。RequestResources 已经使用 asyncio.Semaphore、AsyncExitStack 和 aiolimiter，属于小型组合，无依据重换框架。

本轮已完成的产品修改是缓存用量：保存提供方实际报告的 cache_read_tokens/cache_write_tokens、对应 reports 和 unknown calls，固定数量 SQL 投影随完整点恢复。未报告、零值、非零值、Actor 归属与恢复共 18 个相关消费者通过，证据在 cache-red.txt 和 cache-green.txt。该结果代表计量正确性；真实缓存命中收益由提供方返回值验证。

## 五、落实

主循环已采用公开 Agent.iter，Tool.from_schema 将固定元工具交给 SDK 顺序执行。ModelProvider 的 request_model 是原有物理流式请求入口，返回原始 ModelResponse、消息序号与未完成原因；普通 request 从同一个结果派生角色视图，记忆提取仍沿用既有协议。生产桥采用公开 Model 子类，避开 FunctionModel 会改写模型名称及估算未报告 token 的测试语义。

激活入口从 Thread 解码一次完整 typed 历史，之后 SDK 将已有消息列表直接借给 request_model；提供方沿单一物理链复用该列表，不再从 Thread 构造第二份全历史。Agent 退出后清除持有历史的运行引用，再进入需要完整 Thread 的记忆提取。完整历史在激活的工具与服务等待期间保持驻留，这一内存寿命变化是明确成本。独立消费者已经验证模型多轮与完整点恢复后的 wire 前缀、每激活一次物化，以及原始 opaque 消息和回执继续可用。

本轮保持 request_limit=policy.max_turns，None 不引入 SDK 默认 50；必需动作等业务纠正使用公开 ModelRequestNode 和完整普通反馈，未添加独立纠正次数限制。SDK 层 retries=0，未声明工具、同响应重复 ID 和无法解析的工具 JSON 作为协议失败保留。length/incomplete/EOF 仍在工具执行前判断；领域异常和取消传播，领域回执在终止后即刻存在，无额外结束请求。对未知工具从“继续纠正”改为“显式未完成”的协议选择已经更新合同与对应消费者。

采用前的 llm.py/models.py/model_messages.py 分别为 618/656/49 行，当前为 647/678/61 行，三个文件净增 63 行（含释放历史、审查修复和原生对象协议），并未取得代码行数缩减。工具分派、节点推进和 SDK 协议处理转交成熟库；新增行是原始 typed 模型桥与业务边界适配。工具/领域 JSON schema 继续复用 jsonschema，记忆提取器及严格 schema 变换没有同时重写。

agent-red.txt 保存 Agent.iter 消费者的失败先行证据，agent-green.txt 保存 146 个 LLM、提供方、终态、恢复、记忆、计量等相关用例通过结果；最后的小组 agent-freeze.txt 八项覆盖独立长历史、超过 50 请求、opaque 恢复和缓存字段。产品代码已交独立全量与真实端点验收，本文件中的离线通过结论不代替它们。

真实 round_robin 首轮未完成的原因是 action_invoke.arguments 传入对象而元工具合同要求 JSON 字符串；模型先正确发现、描述动作，随后重复收到缺少字段位置的 invalid_tool_arguments。所有动态元工具现在统一返回 jsonschema 的字段路径、错误信息、校验项和期望值，帮助主体按已提供的合同纠正。独立消费者发现首次空 Thread 会被 SDK 拒绝，现将内容为空的 user 协议帧明确写入 Thread，保持完整记录。两项均先有失败用例，修复后 78 项相关及独立长历史/恢复用例通过，见 agent-review-red.txt 和 agent-review-green.txt；原真实失败运行保留，针对性重新验收由主控执行。

action_invoke 直接接收原生对象可以省去双层 JSON，是合理的后续简化方向；严格模式下任意动态参数对象的 schema 表达需要同步确定。因此本轮保持原字符串传输合同，以通用校验反馈修复可明确验证的根因，避免同时改变传输规则与纠正信息。

独立全量又发现可选重复读取调温在 required 文字与非法并行纠正后保留上一轮 streak。temperature-review-red.txt 保存实际 [.1,.1,.3,.3,.1] 与合同 [.1,.1,.3,.1,.1] 的两项失败；在这两种反馈边界清零后，temperature-review-green.txt 的 64 项温度、LLM、Agent 与长历史消费者通过。本次修改共三行，恢复既有调温合同。


## 六、动态参数

真实 round_robin 在原 8 轮模型预算下连续两次失败；第二次已附完整 jsonschema 类型反馈，模型仍传原生 arguments 对象。根因是固定元工具为适配严格 schema 而把业务对象再次编码成 JSON 字符串，默认非严格策略也继承了这一成本。默认协议现统一使用原生 action_invoke.arguments、data_query.query 与原生 cursor（行动游标为对象或 null，数据游标由提供方决定类型）；strict_tools=True 保留明确字符串协议，单次边界解码，各模式互斥校验。领域账本始终接收对象，权限、实际 Action schema、原文、预算与回执保持同一入口。Shell 的文本命令自然先解码一次命令 JSON，其业务 arguments 原本就是对象。

公开 SDK 的 Tool.from_schema 直接传递动态对象；本项目继续使用成熟 jsonschema 校验。OpenAI [function calling](https://developers.openai.com/api/docs/guides/function-calling) 明确允许 Responses 显式 strict:false；strict:true 要求每个对象禁止额外属性，因此无法在一个固定 strict 元工具中直接表达任意领域对象。安装版 OpenAIJsonSchemaTransformer 对强制严格对象关闭 additionalProperties，对明确 false 保留开放性。[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 使用独立 inputSchema 和原生 arguments 对象。其自然对应是保留完整 action_describe schema 与服务器端验证；动态展开全部领域工具会改变工具集合与稳定前缀，本次没有引入。

新增 native-protocol-red/green 记录证明默认/strict 两种格式、错误格式反馈、Unicode 嵌套参数、查询与分页游标、实际 Chat/Responses/SIWC SDK HTTP body 中的 schema 及 strict 标志。Chat SDK 会省略 false，Chat 服务默认非严格；Responses 和 SIWC 命名空间明确发送 false。SIWC 离线传输验证不能代替尚未执行的真实账户端点验收。真实 round_robin 在新冻结代码上另行复验，沿用原预算。

原生对象与 opaque 数据游标协议冻结时，相关离线消费者共 97 项通过；其中实际 SDK wire 对照包含 Chat、Responses、SIWC 各自的默认与严格模式。研究日志 `native-protocol-red.txt` 保留先失败证据，`native-protocol-green.txt` 保留改造后通过结果。

完整交互合同复核发现 Page.next_cursor 为 Any，已有惰性数据提供方返回整数。data_list.cursor 因此使用任意 JSON 值 schema 并原样传递，Query.cursor 同样保留原生值；显式 strict 模式仍统一按 JSON 文本传输。新增整数、字符串、数组和布尔游标消费者，红证据在 native-cursor-red.txt。这保持了原有提供方续读能力。
