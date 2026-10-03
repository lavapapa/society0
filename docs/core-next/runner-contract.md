# 运行入口

运行计划把公开配置、插件组合和时间序列交给已有的 compose 与 CodeSchedule。Runtime 继续负责唯一的步骤执行和完整点发布，runner 保存本次运行合同及逐步诊断，输出可供独立观察和工作台读取的运行目录。

## 一、配置

RunPlan 包含 plugins、moments、RunContract 和 schedule 服务引用。RunContract 的 release、dependencies、configuration、time、budgets 描述明确发布版本、固定依赖、插件及主体模型参数、时间范围和预算；credential_env 保存所需环境变量名称，凭据值由提供方插件在进程内取得。研究者在工厂中把这些公开值实际用于插件构造。runner 不推断源码版本，正式运行须提供已发布版本并从相应部署执行。

runner.json 在首个模拟步骤前创建并同步落盘，失败会终止启动。文件绑定实际 run_id、插件名称及依赖、实际 Runtime 容量和激活预算、阶段执行模式和阶段容量，以及恢复来源完整步骤。配置文件描述期望值，effective_schedule 保存实际调度值，便于核对。moments 显式给出本次执行时间，恢复时步骤编号从所选完整步骤之后继续；工厂须给出与本次新时间范围相符的公开合同。

## 二、执行

Python 使用 `await run_plan(path, plan, source=None, step=None)`。source 与 step 通过 compose 创建新的运行分支。每个 moment 调用一次已有 schedule.run_step，完整描述符决定成功范围。领域错误和取消继续向调用方传播；runner-status.json 记录错误类型及最新完整步骤，Thread 与结果保存各自的事实和失败诊断。

`timings.jsonl` 每次尝试只追加 Runtime.last_timing 的短数值。该诊断写入失败增加 diagnostic_errors 并报告日志，已完成步骤保持其真实发布状态。runner-status.json 使用独立进度快照；该文件缺失或落后时，应查询完整描述符确认范围。

命令行通过 `python -m society0.kernel.runner --factory module:function --config CONFIG.json --output RUN_DIR` 调用同步计划工厂。恢复增加 `--source SOURCE_DIR --step N`。成功输出结构化运行结果；失败返回非零状态及错误类型。具体请求原文和业务异常保留在运行事实中，命令行不会将异常字符串自动复制到标准输出。

## 三、示例

`examples.core_next.rule_run:build` 展示两个规则主体的短运行，使用真实 Runtime、结果表与完整恢复协议。公开配置至少提供 steps 和 release，例如 `{"steps":2,"release":{"commit":"填写实际已发布提交"}}`。占位发布标识用于说明字段，正式运行将其替换为真实部署身份。该示例无需模型、记忆、Chroma 或 shell 可选依赖。

LLM 与记忆组合使用 services-contract 中的标准 thread_plugin 和 memory_plugin，提供方配置使用 models-contract。运行入口和领域计划分别负责执行协议与业务选择，生命周期统一交由 PluginHost 管理。
