# Society0 Core 最终候选 2590cf3 独立复审

审查对象是 `2590cf3a7d5c2bcee71867cfbc1e71a890edcdcb`。本轮从公开 `compose`、`RunPlan/run_plan`、`PluginHost`、`Information/Actions`、`Runtime/CodeSchedule`、`LLMDriver/ModelProvider/Thread` 到正式 pilot、工作台消费者追踪调用链，并核对 PRD、SDD、能力表、验收映射、TODO、发行说明和性能原始报告。没有调用外部模型、真实端点或子智能体，也没有读取凭据、修改产品源码、TODO 或提交。

## 一、结论

本次离线源码复审**没有发现新的产品代码阻断**。第一轮的两个阻断已在精确候选中修复：SQL 目录按子路由执行 `discover`，行动处理器抛错、取消或返回无效值时使共享步骤失效。`session_transport=None` 不自动向提供方发送会话 metadata，`metadata` 模式显式发送稳定的 Thread 会话身份；真实 SDK MockTransport 的 400 场景、重试及完整消息续接通过。

**暂不把此提交判为可作为最终 Core 验收合并。** 当前可继续作为离线通过的候选；真实网络 15 项、正式 starter 真实链、V03 旧新同语义联合对照及最后源码冻结仍在外部收口。能力矩阵和 TODO 已列出这些待办，本报告没有把确定性替身或本轮 Node pilot 冒称为真实模型验收。若这些证据在同一最终源码身份上闭环，且无新增阻断，可再作合并判断。

## 二、修复

`Information.list/list_files` 进入 `SQLInformation.list_authorized` 后，目录逐个构造子路由 `Ref` 并调用权限函数。返回项和 `total` 只含允许发现的路由；游标身份绑定主体、moment、运行身份、权限依赖版本与实际可见路径。权限表变更、换时点和换运行的续页被拒绝。原独立失败例、作者边界例以及本轮全量均通过。直接访问子路由仍由公共入口单独判定权限；原文读取和行级查询保持各自的 `read` 与 SQL 授权合同。

`Actions.invoke` 在处理器边界把 `BaseException` 记入激活 scope；同一步所有激活共用故障状态，`PhaseContext` 把故障传给 Runtime，使 `StageStore.complete` 不执行。取消、同步与异步异常、无效返回、Driver 捕获后继续、普通无修改业务 `rejected` 的既有边界测试均通过。新增 [并行主体回归](../../tests/primary/test_core_final_review_r2.py) 在 `Phase(execution='independent', capacity=2)` 下验证：主体 A 已写入后抛错，主体 B 随即尝试另一行动，后者不执行，完整水位保持 0，独立恢复目录没有两笔 dirty 事实。该测试在精确归档快照中单独通过。对于领域插件正常返回 `rejected` 的语义，插件仍须遵守其业务拒绝不修改事实的合同；本轮没有把错误插件行为伪装为 Core 新限制。

`LLMDriver` 保持完整 Thread 并把请求选项交给 `ModelProvider`；后者按 profile 决定是否追加 metadata，保留调用方已有 `extra_body` 字段，不修改原配置。物理重试固定请求水位，`Thread.read_request` 可重建当次实际完整消息，响应原始字段和失败诊断仍入 Thread。`test_kernel_session_transport*`、`test_kernel_models_review.py`、`test_kernel_llm_review.py` 及全量覆盖了 SDK 编码、503 重试、长历史、取消无成功回执和工具重复回执；本轮没有使用网络提供方。

## 三、验证

当前工作树的 `src/` 与该提交无差异；为排除并行研究文档及测试污染，使用 `git archive 2590cf3a7d5c2bcee71867cfbc1e71a890edcdcb` 解到 `/tmp/society0-review-r2-2590cf3`，仅把本轮新增测试复制进快照，在该目录以本工作树 `.venv/bin/python` 执行。`python -m pytest -m 'not real_e2e'` **665 通过、15 个 real_e2e 被显式排除**，执行输出见 [确定性日志](final-review-r2-20261004-deterministic.txt)。本轮独立测试单独执行也 **1 通过**，见 [并行 scope 日志](final-review-r2-20261004-scope.txt)。这些结果包含原独立 2 项及作者 7 项修复边界测试，也覆盖公开入口、插件装配、规则/LLM、记忆、恢复、观察、正式 starter 的确定性路径。

显式 `tests/experiments` 为 **59 通过、1 跳过**，唯一跳过是主环境缺少隔离的 sqlite-vec 组件，见 [实验日志](final-review-r2-20261004-experiments.txt)。随后从指定 `/tmp/society0-vector-study-20261004` 的 site-packages 提供该可选组件，精确快照上三条向量后端试验 **3 通过、0 跳过**，见 [向量日志](final-review-r2-20261004-vector.txt)。`tests/performance` 为空，故没有把 pytest 的无测试退出码当产品失败；性能判断依赖 primary、experiments 的行为测试与冻结测量报告。

复用 `examples/core_next/conversation_pilot.py` 在快照中运行两轮共享环境 pilot，`export_payload` 生成实际 Core 工作台输入，再设 `CORE_NEXT_WORKBENCH_PAYLOAD` 执行 Node 全量。`npm test -- --test-concurrency=1` **9 通过、0 跳过**，第九项实际渲染表格与趋势，见 [Node 日志](final-review-r2-20261004-node.txt)。默认并发的 `npm test` 曾使两份 Vite 测试争用 WebSocket 端口并停滞；串行参数使相同九项稳定完成。此为当前 Node 测试执行条件，CI 完整验收应固定该调用方式；本轮没有修改测试脚本。

## 四、性能与范围

[性能综合报告](performance-acceptance-20261004.md) 的真实大根 1,448,471 条逐值与顺序对照、约 226.54 MB 源目录、单大 entry、独立恢复与范围读取、固定活动扩历史索引、规则活动规模、确定性 LLM/Memory 完整步骤及 32 核线程/进程成本，足以支持**已测 Core 存储、codec、查询与计算路线**的验收判断。归档候选相对 X08 的 `b51c5f9`，`storage/_json_chunks/datasets/threads/memory/results` 与 `resource_managers` 没有产品差异；变化集中在本次已复测的授权目录、行动故障、Runtime 和模型会话传输。旧 X08 的模型步骤绝对计时仍属于其冻结源码，不能迁移成当前 SDK 路径的性能实测。

报告已经明确的范围是当前本地同文件系统硬链接、操作系统页缓存保留的独立进程冷读、短 20 步与固定活动量试验。巨大单值整值改写、长期 changeset 链、Memory/Chroma 的高驻留、增长的 LLM/Memory/Shell 并发、长读者 WAL 峰值、跨文件系统复制和真实产业机制资金/库存热索引，均没有整系统规模保证。这些是 V04 广义规模边界及领域迁移待验项，不能据此否定已测的 Core 机制，也不能把部分测量升级为完整长期仿真收益承诺。性能改进保持完整原文、Thread、主体可见信息与行动预算，旧单体格式和安全性研究不在本轮目标内。

本轮结果允许继续推进当前候选的外部验收；最终合并仍需把真实 15 项、正式 starter、V03、能力矩阵和最后源码身份一起核对。若产品源码在收口期间变化，受影响组与确定性、Node 全量需按新提交重验。
