# 行动与时长诊断

诊断使用规范执行点保存的事实及短投影。原文通过 Thread 的事件序号和范围读取保留，累计查询服务于运行观察与工作台；当前记录和所选完整步骤具有不同的恢复范围。

## 一、行动

`Observation.action_summary(actor=None, error_limit=5, max_bytes=65536)` 返回 scope=`llm_tool_actions`。action_counts 按动作名统计 LLM 实际进入的工具尝试，successful_action_counts 统计 completed，accepted_action_counts 保留 accepted，failed_action_counts 统计 rejected、error 和 cancelled。action_tag_counts 统计完成动作的去重标签；action_duration_summary 保存每类已结束工具的 count、total_s、max_s。error_samples 返回最近有限条失败的 thread_id、start_seq、finish_seq，可继续读取完整参数、结果或异常。

一个 shell 脚本内每次领域 invoke 独立登记，外层 bash 调用不额外增加动作数。领域 handler 内部直接调用其他 handler 属于该显式工具的实现过程，累计值保持外层一次。已保存回执被再次读取或重放时沿用原事实，不登记新的执行。DriverResult.action_counts 用于本次激活的预算与回执语义；工作台使用上述实际执行投影，避免把重放产生的账本视图再次相加。RuleDriver 中的任意 Python 行为由领域 StepResult 指标明确表达。

ThreadStore.start_action 保存完整 action_started 并返回事件序号；finish_action 以该序号绑定结果、标签及耗时，原文和短投影在一个事务提交。关闭 LLM Thread 时，closed 事件携带 outcome、reason、elapsed_s 与 phase_timings，累计 activations 按状态与终止原因分组。失败步骤中已落盘的调用仍是 live 诊断；准备 complete view 后看到该完整步骤内的事实与投影，恢复不携带后续未完成事实。

## 二、时长

resource_usage 的 duration_s、queue_s、jitter_s、provider_s 是各物理尝试的墙钟时长累计，每项对应 reports 表示实际测量次数。queue_s 是客户端获得并发许可前的等待，包含事件循环调度；jitter_s 是配置的额外延迟；provider_s 测 SDK 请求等待，包含网络、服务端处理与客户端等待，无法据此进一步区分服务端排队。duration_s 从本次尝试开始测至终态事实写入之前，包含排队、延迟、SDK 等待和本地处理。物理重试分别保存，外层重试间隔进入激活的 model_s。多个并发请求的累计秒数可以超过整个运行墙钟时间。

LLM closed 事件中的 phase_timings 记录 prompt_s（输入构建与保存）、memory_recall_s（召回钩子及上下文保存）、setup_s（工具合同和工作区准备）、model_s（决定阶段提供方调用）、tools_s（实际元工具执行）、memory_write_s（记忆写入钩子，包括提取模型和嵌入）、cleanup_s（工作区封存与关闭）、other_s（剩余循环开销）。该组阶段顺序测量，同一阶段多次执行累计；elapsed_s 截止写入 closed 之前。action_duration_summary 是 tools_s 内部的领域工具部分，物理模型/嵌入等待又分别包含于 model_s 或记忆阶段。父阶段和子阶段不能相加当作完整运行耗时。

每项 phase_timings 只在激活结束时和 closed 原文一同写入短累计表；异常与取消也沿实际退出路径记录。提供方原终态事件保存短 timing 字段，完整消息与响应不因计时再次复制或序列化。

## 三、步骤

Runtime.last_timing 保存最近尝试步骤的 step、phase_s、finalization_s、complete_s、total_s、failed 和 complete_step。phase_s 包含各阶段执行、等待、退出清理和阶段结果保存；finalization_s 包含步骤结果保存与 complete 发布，complete_s 是其中的发布调用。total_s 截止最终进度快照写入之前。普通环境阶段、主体决策阶段及机制 hooks 仍可按 Results 的阶段名称和 elapsed_s 分别读取。

这些步骤时长是诊断数字。Progress 将它们放入独立短快照，写入故障不更改业务事实；完整描述符继续决定已经完成的范围。发布失败同样保留尝试时长及已完成下界。runner 负责在本次运行清单对应的产物中保存逐步时长，持续分析无需扩展 Core 的权威恢复协议。SQL 事务和原文持久化发生于多个业务阶段，上述 complete_s 仅指完整步骤发布，不代表整个步骤所有存储 I/O。

这些接口提供可追溯的诊断范围。全局物理用量与共享批次的主体归属仍按 models-contract 的规则读取，阶段累计用于定位工作量和等待来源，不替代受控的前后性能对照。
