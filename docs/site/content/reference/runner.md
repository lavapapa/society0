# RunPlan 与运行入口

`from society0.kernel.runner import RunContract, RunPlan, run_plan` 将公开研究配置、插件及调度服务绑定到一个独立运行目录。

## 一、计划

运行工厂负责将合同中的公开参数实际用于机制构造。

<!-- api: society0.kernel.runner.RunContract -->
<!-- api: society0.kernel.runner.RunPlan -->
<!-- api: society0.kernel.runner.run_plan -->

RunContract 记录源码发布身份、固定依赖、研究配置、时间与预算；credential_env 保存环境变量名称，凭据值由服务在进程内读取。runner 将合同转成独立 JSON 值后安装服务，非 JSON 值与非有限数会导致启动失败。runner 不推断部署源码身份，也不自动执行合同里描述的预算；实际容量与激活预算来自 Runtime。

RunPlan.plugins 是插件序列，schedule/runtime 为服务引用。`await run_plan(new_path, plan)` 返回含运行身份、最终状态、完整步骤与诊断错误数的字典。恢复提供 source 与可选 step，交给 compose 创建新目录。领域异常和取消向上传播。

SequenceSchedule 的 times 要保留整个研究时间序列。恢复后以已完成 step 索引下一项，切勿把 times 改成剩余时间后再次使用原步骤编号。自定义 Schedule.next_step(completed_step) 返回 StepPlan 或 None，读取计划不应在 Runtime 外提交领域变化。

## 二、命令

CLI 接受同步的 `module:function` 工厂；工厂参数为解析后的配置 JSON，返回 RunPlan。

<!-- api: society0.kernel.runner.main -->

```bash
python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config config.json --output run-new
```

恢复追加 `--source run-old --step 1`，目标仍选新目录。成功输出结构化结果；失败返回非零状态和错误类型，原业务信息沿对应事实与诊断保留。示例工厂的 start/end 均包含在范围中；配置细节见[首次计划教程](<../tutorial/first-plan.md>)。

## 三、产物

运行清单与诊断各有独立的故障边界。

首个模拟步骤前必须写入并同步运行清单。它记录合同快照、原始插件根顺序、Host 展开的关系图、实际 run_id、effective_runtime 以及恢复来源。清单失败会终止启动。

每次尝试追加短时长诊断，并更新运行状态快照；这些诊断 I/O 故障计入 diagnostic_errors，已发布步骤保持原完整身份。状态文件可能缺失或落后，恢复范围以 StageStore 完整描述符为准。结果事实写入失败则使步骤失败，不能按诊断错误略过。

run_plan 退出时关闭整个组合，后台任务和连接由插件拥有并收束。它不自动重试动作，不自动覆盖旧运行目录；总成本是所选步骤、插件准备、完整发布与必要恢复复制的成本之和。

恢复操作见[恢复教程](<../tutorial/recovery.md>)，源码见 [run_plan](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/runner.py#L42>)。
