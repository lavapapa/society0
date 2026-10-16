# RunPlan 与运行入口

`from society0.kernel.runner import RunContract, RunPlan, run_plan` 把一个公开的研究配置、一组插件和调度服务绑定到独立的运行目录上，然后逐步执行。调用者要给出目标目录（恢复时还要给出源运行与完整步骤）、凭据所在环境变量的名称，以及一个能返回 RunPlan 的工厂。运行目录、整个组合和后台任务的关闭都由 `run_plan` 这一层负责。

## 运行计划里记什么

工厂的职责是把运行配置里那些公开参数真正用到机制构造上。

<!-- api: society0.kernel.runner.RunContract -->
<!-- api: society0.kernel.runner.RunPlan -->
<!-- api: society0.kernel.runner.run_plan -->

RunContract 记录源码发布身份、固定依赖、研究配置、时间和预算；credential_env 里存的是环境变量名称，凭据值由服务在进程内读取。runner 会先把 RunContract 转成独立的 JSON 值，再安装服务；值无法 JSON 化或出现非有限数会导致启动失败。runner 不会去推断部署用的源码身份，也不会照着 RunContract 里写的预算自动执行；实际容量与激活预算来自 Runtime。

RunPlan.plugins 是插件序列，schedule 和 runtime 是服务引用。`await run_plan(new_path, plan)` 返回一个字典，里面有运行身份、最终状态、完整步骤和诊断错误数。恢复时传入 source 和可选的 step，交给 compose 创建新目录。领域异常和取消会继续向上传播。

SequenceSchedule 的 times 要保留整个研究时间序列；恢复之后按已完成的 step 索引下一项，不要改成剩余时间却继续用原来的步骤编号。自定义 Schedule.next_step(completed_step) 返回 StepPlan 或 None；读取计划时不要在 Runtime 之外提交领域变化。

## 命令行入口

命令行接受同步的 `module:function` 工厂：参数是解析后的配置 JSON，返回值是 RunPlan。

<!-- api: society0.kernel.runner.main -->

```bash
python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config config.json --output run-new
```

恢复时在命令后面加 `--source run-old --step 1`，目标目录仍然是新的。成功时输出结构化结果；失败时返回非零状态和错误类型，原业务信息保留在对应的事实与诊断里。示例工厂的 start 和 end 都包含在范围内；配置细节见[首次计划教程](<../tutorial/first-plan.md>)。

## 运行清单与诊断

运行清单和逐步诊断各自有独立的故障边界。

第一个模拟步骤之前，runner 必须把运行清单写入并同步到磁盘。清单里有 RunContract 的快照、原始插件根顺序、Host 展开后的关系图、实际的 run_id、effective_runtime 和恢复来源。清单写失败会终止启动。

每次尝试都会追加一条很短的计时诊断，并更新运行状态快照；这些诊断文件读写失败只计入 diagnostic_errors，已经发布的步骤保持原来的完整身份。状态文件可能不存在或落后，恢复范围以 StageStore 里的完整描述符为准。结果事实写失败会让该步骤失败，不能当成诊断错误放过。

run_plan 退出时会关闭整个组合，后台任务和连接由拥有它们的插件结束。它不会自动重试动作，也不会覆盖旧的运行目录；总成本来自所选步骤、插件准备、完整发布，以及恢复时必要的数据库复制。

恢复操作见[恢复教程](<../tutorial/recovery.md>)，源码见 [run_plan](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/runner.py#L42>)。
