# LLMPolicy 与 LLMDriver

`from society0.kernel.llm import LLMPolicy, LLMDriver`。策略定义一次激活的决定方式；驱动在绑定主体的 Session 中推进模型、文件读取与领域行动，返回 `DriverResult`。正式运行通过[驱动装配](actor.md)提供工厂。

## 一、接口

将已安装的模型与 Thread 服务交给驱动，`input_builder(session)` 可同步或异步，负责本次新增输入。

<!-- api: society0.kernel.llm.LLMPolicy -->
<!-- api: society0.kernel.llm.LLMDriver -->
<!-- api: society0.kernel.llm.LLMDriver.run -->

`provider_selector(session)` 在激活开始选择一次提供方。`extensions` 按顺序建立认知作用域；InputBatch 的消息与消费位置先保存，随后执行 effects。提供方、Thread 和扩展服务由装配者持有。

## 二、决定与状态

`decision` 使用行动发现、描述、执行和文件工具。`action_find` 对名称及描述做忽略大小写的字面子串匹配，空字符串用于分页枚举。名称与标签许可共同限制动作；required 条件按成功完成核对。`accepted` 保留已受理语义，`completed` 才计入成功条件；成功终止动作触发结束。

轮次、行动总次数和逐动作次数分别计量，失败尝试消耗行动预算。达到硬上限、上下文超限或提供方输出 `length` 时返回 `incomplete`；截断响应中的工具调用不执行。领域处理器异常、取消和必需留证失败向 Runtime 传播。同一 call_id 的已保存回执被复用，相同 ID 搭配不同调用会被拒绝。

`strict_tools=False` 使用原生动态 JSON 对象；严格模式将动态参数声明为 JSON 文本，在分派边界解码，领域 schema 与权限始终校验。空响应与重复读取的调温是显式可选策略，受原轮次预算约束。reasoning_stages 提供文字指导及原文分段位置，阶段之间没有强制请求。

`interview` 为结构化测量提供 `submit_result`；声明 result_schema 后校验输出，direct_json 显式启用提供方 JSON schema 响应模式。决定动作不自动加入访谈工具集。

## 三、历史与成本

同一主体、Moment 和 mode 复用 Thread 与 provider_session_id；completed、waiting 可继续，未完成 Thread 要求显式恢复。此前完整消息持续进入请求，历史分页不用于上下文裁剪。每次激活的 typed 历史在模型和工具等待期间驻留，成本随完整上下文增长。

返回值的 value 包含 thread_id、结构化结果及行动计数，reason 解释终止。成功或 waiting 退出时保存工作区并收束扩展；保存失败会使激活未完成并阻止步骤发布。记忆提取在 Thread 最终关闭前执行。

以下为已有服务上的装配片段，输入由研究任务明确提供：

```python
from society0.kernel.llm import LLMDriver, LLMPolicy

def make_driver(provider, threads, input_builder):
    return LLMDriver(provider, threads, input_builder=input_builder,
                     policy=LLMPolicy(mode="decision"))
```

继续阅读[模型指南](../guide/models.md)、[完整认知](../concepts/cognition.md)和[工具 schema](tools.md)。实现入口为 [LLMDriver.run](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/llm.py#L381)。
