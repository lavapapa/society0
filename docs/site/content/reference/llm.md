# LLMPolicy 与 LLMDriver

`from society0.kernel.llm import LLMPolicy, LLMDriver`。策略决定一次激活用什么方式作决定；驱动在绑定主体的 Session 里推进模型、文件读取和领域行动，最后返回 `DriverResult`。调用者要自己提供模型提供方、Thread 服务和 `input_builder`，并在正式运行时通过[驱动装配](actor.md)把工厂交给主体目录；提供方、Thread 和扩展服务都由你在装配时提供并持有。

## 装配驱动时要给什么

把已经装好的模型服务和 Thread 服务交给驱动，`input_builder(session)` 负责给出本次新增的输入。

<!-- api: society0.kernel.llm.LLMPolicy -->
<!-- api: society0.kernel.llm.LLMDriver -->
<!-- api: society0.kernel.llm.LLMDriver.run -->

`input_builder(session)` 可以同步也可以异步。`provider_selector(session)` 在激活开始时选一次提供方。`extensions` 按顺序建立认知作用域；InputBatch 的消息和消费位置先保存，之后才执行 effects。

## 一次决定里的行动、状态与上限

`decision` 模式下，驱动使用行动发现、行动描述、行动执行和文件这几类工具。`action_find` 对名称和描述做忽略大小写的字面子串匹配，传空字符串则分页枚举。名称许可和标签许可一起限制哪些动作可用；required 条件按成功完成来核对。`accepted` 保留已受理的含义，只有 `completed` 才计入成功条件；成功终止动作会触发结束。

轮次、行动总次数和每个动作的次数分别计量，失败的尝试同样消耗行动预算。达到硬上限、上下文超出限制，或者提供方返回的结束原因是 `length` 时，驱动返回 `incomplete`；被截断的响应里的工具调用不会执行。领域处理器抛异常、任务被取消、必需的留证失败，都会向 Runtime 传播。同一个 call_id 已经保存过的回执会直接复用，同一个 ID 配上不同调用会被拒绝。

`strict_tools=False` 时动态参数用原生 JSON 对象；严格模式把动态参数声明为 JSON 文本，在分派边界解码，领域 schema 和权限始终校验。空响应和重复读取的调温是显式开启的可选策略，仍受原来的轮次预算约束。reasoning_stages 提供文字指导和原文分段位置，阶段之间没有强制请求。

`interview` 模式为结构化测量提供 `submit_result`；声明 result_schema 后会校验输出，direct_json 显式启用提供方的 JSON schema 响应模式。决定类的动作不会自动加进访谈工具集。

## 专用动作的工具暴露

默认工具集是固定的。要让某次激活额外暴露配置选定的领域专用动作，装配时给驱动一个选择器，由它在激活开始时给出`(动作名, 目标 Ref)`的有序序列或单项。

<!-- api: society0.kernel.tool_exposure.ToolExposure -->
<!-- api: society0.kernel.tool_exposure.ExposedAction -->
<!-- api: society0.kernel.tool_exposure.selection_items -->
<!-- api: society0.kernel.tool_exposure.tool_alias -->

选择器只声明身份，不携带 schema：驱动通过活动注册表取得这些动作的授权描述，并把参数 schema 原样追加在固定工具之后，顺序与选择器给出的顺序一致。选定但当前不可用的动作让这次激活失败，不会悄悄从工具数组消失；注册表里的权限、对象可用性和标签校验照常生效。别名把动作名转写为合法的提供方函数名（`industry.set_price` 变成 `industry_set_price`），与固定工具重名直接报错。

专用动作的调用与 `action_invoke` 走同一本账：行动预算、per-action 限制、strict 归一、回执复用和成功/失败记法完全一致，驱动不会为它们另建执行路径。`action_find`、`action_describe`、`action_invoke` 三个通用入口保持原样，仍然可以发现和调用全部可用动作。`direct_json` 与 `interview` 模式不注入这些工具。

选择器可以是同步或异步回调，也可以装配成具名服务再由驱动的 `tool_exposure` 参数引用。这段代码只展示接线，动作本身仍由领域插件注册：

```python
from society0.kernel.interaction import Ref
from society0.kernel.tool_exposure import ToolExposure

def select(session):
    return [("industry.set_price", Ref("industry", "actor", session.actor.id)),
            ("industry.set_production", Ref("industry", "actor", session.actor.id))]

tool_exposure = ToolExposure(select)
```

## 准备阶段可用的决策作用域

扩展的 `prepare` 回调原先只能读会话游标；需要在召回这类辅助请求里复用同一次决策的输入时，驱动在 `context.prepare()` 之前填好一个只读作用域。

<!-- api: society0.kernel.llm.DecisionScope -->
扩展上下文的完整接口见 [ActivationContext](cognition.md)。

`DecisionScope` 给出决策 Thread、当时的输入水位 `through`、实际使用的提供方实例、路由抽样点、端点标识、本次进入请求的有序工具数组、已暴露动作以及激活租约。辅助请求据此读取同一段前缀并使用同一提供方，不会重新构建一份经营输入。作用域只在本次激活有效，`prepare` 之后驱动仍按原有顺序装配外壳与文件服务。

## 本次激活的完整读取事实

`LLMDriver` 在每次激活开始时将 `session.cursors['read_facts']` 重置为空的 `set[Ref]`。已完成的只读 Action 通过结果 `value['facts']` 提供 Ref 对象的字典表示，驱动将这些事实加入游标；`accepted`、`rejected` 结果保持游标原状。普通写动作可以清空重复读取提示使用的事实集合，本次激活已读取的消费凭据继续保留。

原生 `read` 仍由 ActorFiles 执行授权、版本校验和原文读取。驱动按返回的 `source` Ref、`revision` 与 `total_bytes` 区分文档读取，将本次激活实际返回的字节区间合并；从字节0到全文末尾连续覆盖后，才把 source 加入 `read_facts`。分块续读使用 `next_offset`，UTF-8 尾部尚未呈现的字节留到下一块。仅读取尾部、区间有缺口或跨版本拼接均无法形成完整读取凭据。目录与记录导航的 Ref 保留其自身身份，领域消费者据此区分导航记录和正文。

游标随本次激活临时存在，同 Moment 的下一次激活重新累计。领域扩展在收尾时读取该集合，并结合 `ActivationContext.result.status` 决定成功消费；持久化消费状态、收件范围及确认操作由领域规范写入器负责。失败或截断期间已取得的读取事实供本次诊断使用，成功消费须等待本次结果为 `completed`。实现入口为 `_Ledger.apply_effect`、`_Ledger.read_effect` 与 `LLMDriver._dispatch`。

## 历史、保存与开销

同一主体在同一个 Moment、同一个 mode 下复用 Thread 和 provider_session_id；completed 与 waiting 可以继续，未完成的 Thread 需要显式恢复。此前保存的完整消息会继续进入请求，历史分页不用于裁剪上下文。每次激活的 typed 历史在等待模型和工具期间一直驻留，开销随完整上下文增长。

返回值的 value 里有 thread_id、结构化结果和行动计数，reason 说明为什么结束。以成功或 waiting 退出时会保存工作区并收束扩展；保存失败会让这次激活变成未完成，并阻止步骤发布。记忆提取在 Thread 最终关闭之前执行。

以下为已有服务上的装配片段，输入由研究任务明确提供：

```python
from society0.kernel.llm import LLMDriver, LLMPolicy

def make_driver(provider, threads, input_builder):
    return LLMDriver(provider, threads, input_builder=input_builder,
                     policy=LLMPolicy(mode="decision"))
```

继续阅读[模型指南](../guide/models.md)、[完整认知](../concepts/cognition.md)和[工具 schema](tools.md)。实现入口为 [LLMDriver.run](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/llm.py#L381)。
