# 行动、用量与时长诊断

诊断通过 `society0.kernel.observation.Observation` 读取规范写入点维护的短投影。原参数、响应和错误保存在 Thread 或 ResourceCalls，摘要中的引用用于继续读取原文。调用者给出运行目录并关闭读取对象；诊断读到的数字来自运行期间保存的计数。

## 行动统计

行动统计的 scope 为 `llm_tool_actions`，覆盖 LLM 实际进入的领域工具调用。

<!-- api: society0.kernel.observation.Observation.action_summary -->
<!-- api: society0.kernel.threads.ThreadStore.start_action -->
<!-- api: society0.kernel.threads.ThreadStore.finish_action -->

action_counts 统计尝试；successful_action_counts 对应 completed，accepted_action_counts 保留受理，failed_action_counts 包含 rejected、error、cancelled。完成标签去重后计数，错误样本给出 Thread 和事件序号。error_limit 控制最近样本数量，max_bytes 容纳不了摘要时失败并提示调整预算。

一个 shell 脚本中的每次 invoke 单独计数，外层 bash 不重复计入。领域 handler 内的内部调用归于本次工具实现；已保存回执的复用不新增执行。DriverResult 的行动账本用于激活预算，工作台使用实际执行投影。规则主体行为由领域 StepResult 指标表达。

## 物理用量

请求统计区分实际物理尝试与逻辑嵌入使用，保持提供方真实报告值。

<!-- api: society0.kernel.observation.Observation.resource_usage -->
<!-- api: society0.kernel.threads.ThreadStore.record_provider_request -->
<!-- api: society0.kernel.threads.ThreadStore.record_provider_event -->

requests 统计物理尝试，responses 统计收到响应，errors 包括提供方或解码失败。响应到达后解码失败会同时增加 responses 与 errors，这些列不可相加作为调用总数。每项 token 和缓存计数配有 reports，区分未知与明确零值；unknown_usage_calls 包含未报告规范 total_tokens 的尝试。系统不推算缺失 token、价格或缓存命中。

共享嵌入批次在全局计一次，embedding_uses 统计逻辑使用，缓存命中仍关联原批次。全局 attribution 为 physical_calls_once；主体为 related_physical_calls_not_additive，多个主体可能关联同一完整批次，跨主体求和会重复。逻辑使用来源保存前，进行中的主体统计可能落后于全局。

## 时间与版本

duration_s、queue_s、jitter_s、provider_s 是各物理尝试的墙钟累计，并带报告次数。queue_s 含许可等待，provider_s 含 SDK 请求等待；后者不能分解为纯服务端计算。duration_s 包括排队、延迟、等待和本地处理，多请求并发时累计秒数可超过运行墙钟。

LLM 关闭事件按 prompt、memory_recall、setup、model、tools、memory_write、cleanup、other 保存阶段时长；行动耗时包含在 tools 内，提取与嵌入等待包含在相应记忆阶段。父子阶段不能相加作为总耗时。

Runtime.last_timing 保存最近尝试的阶段、收尾、发布和总耗时，progress 可携带这份诊断。complete_s 测量完整步骤发布调用，其他业务阶段同样发生存储 I/O。live 摘要包含未完成事实；prepared complete view 读取所选完整步骤，恢复沿同一事实边界。

```python
from society0.kernel.observation import Observation

def diagnostics(path):
    with Observation(path) as reader:
        return reader.action_summary(), reader.resource_usage()
```

累计查询读取短投影，成本随命中的模型或行动分组及有限错误样本增长；原文通过范围接口单独读取。定位路径见[故障指南](../guide/troubleshooting.md)，视图用法见[Observation](observation.md)。源码入口为 [resource_usage](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/observation.py#L269)。
