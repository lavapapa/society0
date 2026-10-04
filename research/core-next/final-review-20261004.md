# Society0 Core 与插件系统 a0dae58 独立审查

审查对象是 `a0dae58b9f00c7e723bf9fbb6c16e29b0918d4c9` 的产品源码，以及工作树中已冻结的 Core 合同文档。我从公开 `compose`、`RunPlan/run_plan`、`Information/Actions`、Runtime、Thread/Memory、观察服务、正式 starter 和工作台消费者追踪调用链；未参与实现，未调用子智能体、外部模型或真实提供方，未修改产品源码、TODO 或提交。审查期间其他工作者开始修改三个 kernel 文件，因此最终全量使用该提交的 `git archive` 独立快照，并复制本审查新增的两个测试进入快照。当前工作树的并行修复不计入候选结果。

## 一、结论

候选存在两处阻断，当前不宜判定为可发布。最严重的是行动处理器在已提交一笔规范事务后抛错，规则 Driver 捕获异常并继续时，Runtime 仍把半截行动发布为 complete；另一处是 SQL 命名空间目录泄露主体无权发现的数据集名称和总数。精确复现和产品位置见 [findings](final-review-20261004-findings.md)，独立回归用例见 [test_core_final_review.py](../../tests/primary/test_core_final_review.py)。

## 二、发现

### 2.1 部分行动写入误发布：阻断

`src/society0/kernel/interaction.py:410-423` 的行动边界没有将处理器异常记为本步骤不可发布；`src/society0/kernel/runtime.py:246-269` 只看到 Driver 的最终 `completed`。公开 RuleDriver 可以捕获异常继续决策，因而一个已提交事实、却没有完成反馈的行动进入完整恢复链。快照测试的实际值是 `store.complete_step == 1`，合同要求保持 `0`。现有测试覆盖未捕获的 Driver 异常，未覆盖这一外部消费者路径。

### 2.2 目录范围泄露：阻断

`src/society0/kernel/information_sql.py:155-168` 枚举命名空间下所有路由；`Information` 只对父目录执行 `discover`，未对每个子路由过滤。测试允许主体访问 `/world` 和 `public`，拒绝 `private`，实际目录仍返回 `/world/private` 且 `total == 2`，合同要求仅公开一项。直接访问子路由会拒绝，但目录名称和数量已经暴露；同一根目录逻辑也用于 `list_files`。

### 2.3 验收索引未闭环：交付问题

工作树中的 `docs/core-next/capability-parity.md:320-324` 仍把 U01–U05 的新版证据全部写为“待补新入口”，而 TODO 已将 U02 工作台标为完成，正式两轮 starter 也有直接测试。这是能力表与实际证据的矛盾，V01、V03–V08、U01、U03、U04 仍在 TODO 中待验。作为最终产品候选，当前文档尚不能为公开入口和交付状态提供一致结论；本审查未改文档或 TODO。

## 三、验证

在精确 `a0dae58` 快照运行 `.venv/bin/python -m pytest -m 'not real_e2e'`，结果为 **649 passed、2 failed、15 deselected**，失败均为本审查新加的两个最小用例；完整输出见 [快照确定性日志](final-review-20261004-a0dae58-deterministic.txt)。当前工作树首次运行时产品源码尚与提交一致，得到 651 passed、1 failed、15 deselected；随后并行源码修改开始，之后工作树运行结果不用于候选判定。

明确执行 `tests/experiments`，结果为 **59 passed、1 skipped**；`sqlite-vec` 可行性用例要求隔离环境，当前环境未安装该组件，见 [实验日志](final-review-20261004-experiments.txt)。明确执行 `tests/performance`，该目录为空，pytest 返回退出码 5、没有用例，见 [性能组日志](final-review-20261004-performance.txt)。工作台按 `package.json` 的 `npm test` 执行，结果为 **8 passed、1 skipped**；跳过项要求 `CORE_NEXT_WORKBENCH_PAYLOAD` 指向实际运行输出，本次未提供该变量，见 [Node 日志](final-review-20261004-node.txt)。这些实验与 Node 运行均早于并行产品源码修改。

退役映射共列出 51 个旧 primary 文件。我抽查了旧工具选择策略、记忆历史可见性、进程故障完整标记、社交推荐活动池的实际断言，再读新版对应直接测试：新版分别核对请求与有效工具选择的原文、历史时点原文和向量、发布前后进程故障恢复、与旧参考算法逐项相等的推荐 ID 和分数。这四组有实质断言对应。映射文件按文件类别指向多个新版消费者，不能单独证明其余所有旧断言已覆盖；旧三组真实 e2e 的新版实端点验证属于仍未完成的 V05。

已有测试及源码还覆盖逆序插件清理、请求许可释放和等候时懒载完整上下文、完整 Thread 水位、Memory pending 作业恢复、SQL BLOB 范围读、live/complete 观察水位、慢 HTTP 槽位，以及使用确定性提供方的两轮 starter。它们支持相应局部行为，不能消除上述两个发布阻断，也不能代替真实 15 项、完整大根 V04 或主体效果 V06 的验收。

## 四、后续

由产品作者在共用行动/步骤故障边界和目录发现边界修复，再以两个独立用例先行复验，并运行受影响组与精确产品身份的全确定性。随后把能力表、TODO、真实端点与大负载证据对齐，才能重新作发布判断。此次审查保留了失败证据与源身份；工作树中的并行修改须另立源码身份验证。
