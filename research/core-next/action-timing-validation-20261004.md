# 动作与计时验收记录（2026-10-04）

本组围绕实际 LLM 工具执行、物理提供方请求及 Runtime 完整发布建立短投影。原文继续保存在 Thread，工具统计范围为 llm_tool_actions；RuleDriver 的任意 Python 行为通过领域指标呈现。

## 一、证据

动作 API 缺失、提供方时间字段缺失、激活阶段时间缺失、Runtime 最后尝试时间缺失均先记录失败，见 action-stats-red、timing-red、timing-activation-red、timing-runtime-red 的同日文本。shell 消费者首次测试误用 command 参数，真实 schema 为 script；该次失败独立保存为 action-stats-shell-fixture-error，不列为产品缺陷。

初次 action-timing-final-direct-green-20261004.txt 对应 29 个测试文件、192 项通过。独立审查随后发现嵌入重试从逻辑调用开始累计，重复计算前次耗时；修复为每次物理尝试分别起算，首次保留排队，后续子批次独立计时。OpenAI/Ollama 两个独立反例由红转绿；最终 action-timing-final-direct-r2-green-20261004.txt 对应 30 个文件、195 项通过，进程退出码 0。覆盖实际 LLMDriver 与 Bashkit 的多动作脚本、恢复后回执重放、嵌套领域 handler 失败、原文与计数同事务回滚、完整点恢复后的统计范围，以及 Runtime 发布成功和失败。旧资源合同 22 项通过，旧 jitter 瓶颈定位消费者 1 项通过，分别见 timing-legacy-resource-green 与 timing-legacy-phases-green。

## 二、代价

action-timing-cost-green 中固定最新 5 条错误的读取，在 100 与 10000 条动作短记录下 SQLite VM 指令数为 184 与 174。该试验验证累计投影与最近错误的查询路径，未测量完整 Thread 写入吞吐或实际产物磁盘占用。

真实 ModelProvider 的 SDK 替身试验分开控制客户端排队、额外延迟和 SDK 等待，并对照 Thread 原事件与累计投影。SDK 等待包含网络与服务端处理，数据无法进一步分离服务端内部排队。多个并发物理调用的累计秒数、激活父阶段与其工具子阶段具有不同统计口径，不能直接相加。

## 三、边界

本组为确定性消费者验收，独立审查由 spec 接手。真实提供方完整运行、正式 runner 对 last_timing 的持久消费和现有工作台的数据适配仍在后续验收范围内。Progress 的短状态属于可落后的诊断；完整描述符决定恢复边界，Thread 与结果权威写入失败继续中止步骤。
