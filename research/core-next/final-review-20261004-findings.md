# Society0 Core 候选 a0dae58 独立审查发现

审查基点：`a0dae58b9f00c7e723bf9fbb6c16e29b0918d4c9`。仅新增独立失败用例，未修改产品源码。

## 阻断：行动异常被 Driver 捕获后仍发布部分写入为 complete

位置：`src/society0/kernel/interaction.py:410-423` 的 `Actions.invoke` 直接调用处理器，异常既不使共享运行失效，也不撤销此前成功的事务；`src/society0/kernel/runtime.py:246-269` 仅依据阶段/Driver 最终是否抛错决定 `store.complete`。规则 Driver 或外部 Driver 合法地捕获行动异常并继续决策时，Core 无法识别处理器已在异常前提交的部分事实。

影响：一次行动先用规范 `StageStore.transaction` 提交事实，后续处理抛异常且未返回 `ActionResult`；Driver 捕获该异常并返回 `DriverResult('completed')`。Runtime 实际发布 step 1，恢复链包含没有完成回执的半截行动。这违反 PRD 2.2 与 SDD 3.4 的“部分写入后执行故障使当前步骤失效”合同。普通无修改的业务拒绝仍应通过 `ActionResult('rejected')` 表达。

最小重现：`tests/primary/test_core_final_review.py::test_caught_action_fault_cannot_publish_partial_domain_effect`。期望 `run_step` 报故障且 `complete_step == 0`，实际无异常并发布 step 1。与现有 `test_collect_does_not_isolate_domain_exceptions` 的差别是 Driver 捕获了异常；这是公开 RuleDriver 消费者可做到的路径。执行两个独立用例目前为 `2 failed`。

建议由产品作者在 Actions/Runtime 的共享故障边界记录不可发布状态；再次核对同步/异步处理器、取消、业务拒绝与 LLM 多工具调用的已执行回执。修复后验证部分事务写入、捕获异常及恢复只包含上一完整点。

## 阻断：SQL 命名空间目录泄露未授权数据集

位置：`src/society0/kernel/information_sql.py:155-168`。`SQLInformation.list(scope, "/world")` 直接枚举 `_routes` 并以全部路由数构造 `Page.total`，未对每个子路由调用 `Information._allows(scope, "discover", ref)`。`src/society0/kernel/interaction.py:161-164` 在进入提供者前只检查父目录的引用；即使某个子数据集明确禁止 `discover`，其名称与总数仍向主体暴露。`list_files` 在同一根目录复用此逻辑。

影响：目录名称和数量本身属于 PRD 2.1 的受控信息。共享环境中的机制可以允许主体浏览某命名空间，同时禁止其发现其中的私有集合；当前公共信息入口无法维持该范围。直接子路径的访问会拒绝，但已暴露的目录信息无法收回。

最小重现：`tests/primary/test_core_final_review.py::test_namespace_directory_does_not_disclose_hidden_dataset`。`Information` 允许 `public`、拒绝 `private`，挂载同一 `SQLInformation` 后列出 `/world`，预期 `total == 1`，实际 `total == 2`，并返回 `/world/private`。

建议由产品作者在公共信息目录路由边界应用逐项 `discover` 过滤，并核对分页游标、精确 total、`list_files` 与已有调用者；修复后运行上述失败用例及信息/权限相关测试。
