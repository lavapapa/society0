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
