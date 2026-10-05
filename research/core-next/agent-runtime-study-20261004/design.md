# Agent 复用与缓存计量试验

本次检查以当前工作树与安装版 Pydantic AI 2.54.0 为准。目标是减少通用模型调用机制的重复实现，并保留行动事实、原有预算、完整 Thread 和完整步骤恢复。

## 一、边界

Pydantic AI Agent 已具备工具调用、类型验证、结构化输出、结果纠正及可迭代节点。本地探针验证这些能力，另验证直接替换的语义差异：非空截断正文被当作成功、动态 JSON schema 不自动验证、同响应重复 ID 拒绝而跨响应重复 ID 再执行、终止动作后的工具回执留在下一请求节点。默认请求上限为 50，输出/工具纠正次数为有限整数，Society0 的无上限配置必须继续保持。

主循环集成需保留领域 Ledger、响应接受条件、回执防重、全批预算预检、动态 schema、未知工具反馈与完整历史落盘。先评估公开 API 适配成本，再决定集成范围。记忆提取已有两次请求的明确合同，是采用 Agent 结构化输出的优先消费者。

## 二、缓存

客户端保存提供方实际报告的 cache_read_tokens 和 cache_write_tokens。每个计数有对应 reports，缺失与真实零分开。聚合展示 unknown_cache_read_calls 和 unknown_cache_write_calls，计数随物理调用记录、Actor 归属和完整步骤一起恢复。请求参数维持已有合同，不新增缓存控制字段。该改动只增加固定数量投影字段，写入和当前累计读取成本不随 Thread 历史增长。

验收先使用真实 SDK 的离线 HTTP 流输入未报告、零命中、非零命中及写入计数，检查规范用量、主体归属和完整点恢复。现有提供方消息与物理计量回归继续运行。真实缓存收益必须由实际端点另行报告。

## 动态参数的原生对象合同

真实 round_robin 在相同预算下连续两次把 action_invoke.arguments 生成为对象；字符串 schema 与精确类型反馈均未使其完成纠正。默认 strict_tools=False 时，arguments、data_query.query 和两类分页 cursor 统一使用原生对象（cursor 可为 null），工具定义显式 strict:false。strict_tools=True 时，对应字段保持 JSON 字符串协议，在元工具分派边界统一解码；两种模式各自拒绝另一种输入类型。领域 Ledger 继续接收对象，执行相同权限、实际 Action schema、预算、回执与终态逻辑。strict 模式 JSON 文本解析失败的 action_invoke 仍作为失败行动尝试计数。

OpenAI 官方 function-calling 合同允许显式 strict:false；严格 schema 要求每个对象 additionalProperties:false，因此任意动态对象与通用 strict 元工具无法同时表达。Pydantic AI Tool.from_schema 可直接传递对象；现有 jsonschema 和 Action 校验承担实际校验。成熟 MCP 的 tools/call 同样传递 arguments 对象。SIWC 维持命名空间封装与显式 strict 字段；其端点仍须单独真实验收。

验收先覆盖默认/strict schema、跨模式拒绝、嵌套对象与 Unicode、查询与游标原样透传、真实 SDK Chat/Responses/SIWC 请求中的 strict 标志，随后保持完整历史恢复、动作至多一次、预算和领域失败断言，并以原预算复验真实 round_robin。

完整合同回看：Page.next_cursor 是提供方拥有的 Any，data_list 默认 cursor 使用任意 JSON 值 schema {}，包括对象、数值、字符串、数组及 null；Query.cursor 同样原样保留。action_find 的 Ledger 包装确定为对象或 null。两类 strict 游标仍明确 JSON 文本，不自动猜测另一种编码。

## 社交帖子正文发现

帖子元数据与正文分表保留。社交专属 SQLInformation 子类复用 _items，在 posts 记录上增加既有 feed 同名 content_path；运行中的推荐信息类与只读 social_information 工厂共用该实现。构造引用按本页项目数工作，不读正文、不新增 SQL、不改权限、版本算法或存储 schema。通用查询在 _items 后计算页面字节，因此引用自然计入既有 max_bytes 与继续游标。权限仍在正文 read 时核验；原先真实模型八轮耗尽作为独立失败保留。

失败先行用例覆盖 list、query 投影、单条元数据 read、禁止正文读取的查询路径、正文独立权限、非成员、1024 字节分页及完整点的只读与运行恢复。六项均因缺少 content_path 失败后才修改产品。
