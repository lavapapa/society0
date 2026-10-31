# 设计补充（2026-10-29 修复轮）

本文件承载修复轮对 `inputs/society0_next_refactor_v3/docs/` 设计包的补充与修订文字。设计包本身只读；每节由对应簇在自己的 PR 中追加，标题格式 `## <finding-id>：<主题>`，正文首行写明"补充/修订 inputs/society0_next_refactor_v3/docs/<文件> §<节>"。

## 约定

- 各簇只追加本簇小节，不修改他簇小节。
- 需要裁决的事项记录在 `reviews/claude-fix-plan-20261029/02-decisions.md`；已裁决结果见 `evidence/v3fix-e/decisions.md`。

## NEW-5：测试基线只验证当前构建

补充 inputs/society0_next_refactor_v3/docs/12-validation.md §一。

`tests/` 只允许导入当前 `.build`、node 内置模块、同目录支持文件或安装态 `@society0` 包。导入冻结快照（`evidence/pr0*`）、源码树（`packages/*/src`、`packages/*/build`）、主机绝对路径与 env 兜底由 `scripts/check_tests.py` 拒绝；`scripts/test-build.py` 在运行前核验 `@society0/{host,core,schemastery,cordis}` 解析到 `.build` 内，且 build 层测试不含裸 `@society0/` 导入。

## B 存储与事务：取消、COMMIT、历史清理与事件重投

补充 inputs/society0_next_refactor_v3/docs/07-storage.md、06-runtime.md；设计包本身不修改。

### B.1 事务结果合同（补充 07 §2.1–2.2）
- 取消发生在 BEGIN 发出之前：`transaction` 立即以原始取消原因（同一对象）拒绝，事务确定未提交。
- BEGIN 发出之后：等物理执行结束，再按实际结果结算。COMMIT 一旦发出就不再响应取消；取消晚于 COMMIT 而 COMMIT 成功时，返回已提交的回执。
- `SQLITE_COMMIT_UNKNOWN`：结果未知，停止写接纳，worker 退出后 probe。
- `SQLITE_SEGMENT_FAILED`：已提交（附回执），但增量段证据缺失，停止写接纳，live 须从完整点 fork 恢复。推理层将两者统一记为 `persistence_unknown`。
- 其他拒绝原样重抛原错误对象，表示确定未提交。其中包括 COMMIT 语句因 deferred 约束失败但事务仍打开的情形，以及 `SQLITE_TEXT_NOT_WELL_FORMED`。
- 回调只能等待本事务的 SQL 和可被取消信号终止的操作，不得等待同一数据库的其他事务。
- 完整错误码与处置表见 `docs/public-usage.md` 的“事务结果合同”一节。

### B.2 操作 ID 唯一性与重用（补充 07 §2.2 第 46 行）
- 在途重复由宿主拒绝。已提交重复由 worker 依据 `s0_operations` 返回 `OPERATION_EXISTS`。
- 只有在 `transaction` 以确定未提交的结果结束，或 `probe` 返回 `not-committed` 之后，才可以用同一操作 ID 重试。取消后的重试必须等待该 ID 退出在途状态（`reservation.release()` 结束，或 `diagnostics().inflightOperations` 回落）；在途期间收到的 `OPERATION_EXISTS` 不表示已提交。
- **重试会再次执行回调，回调必须幂等，或只包含同一事务内的 SQL。** 需要只执行一次的外部效果由 attempts 账本保证。
- unknown 期间数据库已停止写接纳，不得重用。诊断按物理事务 ID（tx）区分同一操作 ID 的多次尝试。

### B.3 增量段与历史文件的清理（补充 07 第 74、84、120 行）
- 每次提交导出的暂存段 `segments/<revision>.changeset`，在其所属完整点的描述符耐久发布（rename 加目录 fsync）之后，由发布流程删除；root 发布同样删除此前 bootstrap 阶段的暂存段。这是暂存段转为封存段的生命周期收尾，不属于历史数据删除。
- 晚于最新完整点的暂存段属于 live 后缀，关闭、重开和 GC 都不删除它们。
- 发布失败遗留的封存段（`history/segments/*.zst`）以及 `*.pending`、`history/roots/*.pending`，只由显式的 `history.gc()` 清理；打开 run 或发布失败时都不自动删除。
- `gc()` 在本实例内与 publish、fork、compact、export 互斥，并持有发布锁；发布标记的属主可能仍存活时，拒绝执行破坏性清理（`HISTORY_GC_UNKNOWN`）。
- 调用前应确保没有其他进程打开该 run 目录。
- `history/staging/` 的残留与未被引用的 root 文件留待后续 retention 设计。

### B.4 事件重投（补充 06 第 35、67 行）
- 世界中已存在 `(source,id,offset)` 事件行，表示该事件的业务处理及其激活已在产生该行的推进中完成或被有意放弃。
- 重投只在推进 evidence 中记录 `duplicateEventIds`，不再执行 handler，也不再激活。
- seed 或迁移如果只登记“已见”，即表示接受不激活的后果。

## RV3-04：模型请求的物理相位与结果分类

补充 inputs/society0_next_refactor_v3/docs/18-inference-and-reuse.md §四第 3 段。

只有完整收到 400/401/403/404/422 响应（响应体读到 EOF）才判确定失败；能证明请求未发出的判 `not_dispatched`（`create()` 同步失败、abort 在底层 fetch 之前、连接阶段被拒的 code 链）；其余一律 `unknown`。observer 只统计目标端点调用，不读响应体；Codex 的 OAuth refresh 不经 `options.fetch`，不计入 `calls`；`maxRetries:0` 下无库内重试，`calls>1` 的非成功结局记 `unknown` 并记录 `physicalCalls`。ActivationResult 只在 ledger 写入成功后登记 `modelAttempts`；存在 unknown settlement 时原因为 `transport_unknown`；claim 为 `unknown` 时不登记 settlement，原因为 `persistence_error`。

## NEW-3：embedding 的物理结局与 ledger 写入

补充 inputs/society0_next_refactor_v3/docs/18-inference-and-reuse.md §四、prs/PR08.md。

embedding provider 抛错可附带 `physical: not_dispatched|failed|response`；缺省视为 `unknown`。确定失败只拒绝本批，队列继续服务；`unknown`、ledger 写入失败、未取得 claim 的情况毒化队列并停止后续发送。ledger 写入失败以 `EMBEDDING_LEDGER_ERROR` 上抛，不得吞掉；claim 返回 `existing`/`unknown` 时不写 ledger。
