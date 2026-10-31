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

## RV3-08：Thread 大消息的精确字节预检与分块写入

补充 inputs/society0_next_refactor_v3/docs/15-next-lessons.md §2.2、docs/07-storage.md:76,112、docs/16-domain-design.md:37。

Thread 消息在 stringify 前用有界预检核对 UTF-8 字节：`prepareBoundedJson` 走 `jsonByteBound` 的迭代计数（与 `JSON.stringify` 字节数一致），超过 `maxMessageBytes` 时由 `Thread.append`/`Thread.appendInput` 以 `THREAD_MESSAGE_BYTES` 拒绝；顶层 undefined/function/symbol 不是可编码 Json，抛 `PAYLOAD_JSON_VALUE`（对象中同类属性跳过，数组中同类元素按 `null` 计数，保持原生 stringify 语义）。通过后用原生 `JSON.stringify` 生成字节，再经 `textChunks` 按 `artifacts.chunkBytes` 分块、可取消地 stage；`chunkBytes` 小于 6 时以 `PAYLOAD_CHUNK_BUDGET` 拒绝。同步 stringify 与预检不可抢占，峰值由 `maxMessageBytes` 约束；取消只在 stage 分块边界生效。`prepareValue` 语义不变，现有调用方不受影响。

## RV3-05：driver 错误的阶段分类与持久未知

补充 inputs/society0_next_refactor_v3/docs/18-inference-and-reuse.md §四（driver 部分）、prs/PR08.md。

`driver-llm` 的 activation 对全部抛错源只分类一次，统一为 `LlmActivationError{reason,code,stage,cause}`；已分类错误原样传递，cause 保留内层错误对象。reason 取 `InferenceStopReason` 并新增 `setup_error`、`perception_error`、`thread_error`、`internal_error`；stage 标识触发阶段（`db.snapshot`、`db.close`、`thread.open`、`thread.history|prepare|append|appendInput|consumer`、`prompts`、`context.freeze`、`artifact.stage`、`perception.collect|material|commit`、`attempts.probe|undispatched`、`actions.receipts`、`model.prepare`、`actor-tools`、`runtime.acquire`、`runtime.run`、`transaction:<label>`、`unclassified` 仅限未分类的内部不变量）。ActivationResult 的 incomplete evidence 增加 `code`、`stage`、`committed`、`receipt`；`error` 保留 cause 文本。

- 事务按 B §1.3a 的调用方处置表：`SQLITE_COMMIT_UNKNOWN`、`SQLITE_SEGMENT_FAILED` 为 `persistence_unknown`（SEGMENT_FAILED 带 `committed:true` 与真实 receipt，且该 operationId 不进入 requiredOperations；COMMIT_UNKNOWN 的 `committed`/`receipt` 为 null），`SQLITE_TX_OUTCOME_MISSING` 为 `internal_error`，其余确定未提交或 body 错误为 `persistence_error`。
- `view.close` 只在主体成功时上抛 `persistence_error/db.close`；已有主错误时 close 失败不覆盖主错误。
- 取消只在 signal 已中止时按同一 reason 对象、cause 链或原生 `AbortError` 名称识别；`transport_unknown`、`persistence_unknown` 与 `context_limit` 优先于 `cancelled`。lease release 失败保留 `transport_unknown` 与 `persistence_unknown`，其余改写为 `persistence_error`。
- `actor-tools` 的工具动作或回执保存以 `SQLITE_COMMIT_UNKNOWN`/`SQLITE_SEGMENT_FAILED` 失败时上抛 `INFERENCE_PERSISTENCE_UNKNOWN`，经 runtime 得到 `persistence_unknown`；`ACTION_ATTEMPT_UNKNOWN`（外部动作结果未知）仍为 `transport_unknown`。失败回执的 value 增加 `persistenceUnknown`。
- undispatched 回调按真实 claim 结果处理：`{status:'unknown'}` 抛 `ATTEMPT_LEDGER_UNKNOWN`，`existing` 且记录非 settled（pending/unknown）抛 `ATTEMPT_LEDGER_INCOMPLETE`，两者都归 `persistence_error/attempts.undispatched`，不伪造 not_dispatched 结算；`existing` 已 settled 时视为已有耐久事实、不重写记录；只有 `send-permitted` 才写结算。
- 行动 claim 的存储不确定（`ACTION_ATTEMPT_UNKNOWN{storage:true}`）经 `ActionExecutionFailure.storage` 与 binding 判定报 `INFERENCE_PERSISTENCE_UNKNOWN`/`persistence_unknown`，失败回执 `persistenceUnknown:true`、`externalUnknown:false`；无 storage 的外部动作结果未知仍为 `transport_unknown`。


## RV3-06：大访谈结果、ledger 记录上限与重放一致性

补充 inputs/society0_next_refactor_v3/docs/07-storage.md:112、prs/PR10.md:15、PR10-T04。

`AttemptLedger` 与 `Attempts` 新增公开只读 `maxRecordBytes`。Social 测量合同改为 `MeasurementObservation{value,original,source:Json}` 与 `PreparedMeasurement{value,original,source:StoredValue}`，`MeasurementRecord extends PreparedMeasurement`；`StoredValue` 直接使用 `@society0/core/contracts/data` 的导出，DDL 不变，不保留旧 shape 或迁移。

`measurementRepository` 新增 `inlineBytes`（`Schema.natural().min(1).max(16384).default(16384)`，`new-run`/`affectsResults`）、`prepare`（逐字段调用现有 `prepareValue`）与 `readPayload`（`readValue`）。`append` 要求字段已是 StoredValue，内联字段超过 `inlineBytes` 时以 `MEASUREMENT_PREPARE_REQUIRED` 拒绝；同一事务内 `attachValue` 三字段后写入 JSON，`page` 按 StoredValue 解析。`measurements` 在 observe 后先用 `jsonByteBound` 检查：超过 `maxResultBytes` 得 `MEASUREMENT_RESULT_BYTES`，非 Json（toJSON/Date、BigInt、循环）得 `MEASUREMENT_RESULT_INVALID`；非循环共享引用按原生 JSON 展开语义计数，不误判超限。

访谈 attempt：观察失败时以 `MeasurementFailure` 保留原始文本（`toString` 返回该文本，Error/TypeError/plain string 首次与重放逐字一致），failure 一律存为 StoredValue（4096 内联阈）；成功保存 `PreparedMeasurement`。信封包含 observation/failure/original/evidence 的全部引用；`bytes(envelope)+256` 超过 `attempts.maxRecordBytes` 时把信封整体 stage 为 evidence，再 settle `{envelope:ref}` 并携带其内部全部 refs。重放用 `attempts.readArtifact` 读取信封与 failure，正文引用先 `materialize` 再随仓库引用，避免为读信封制造未引用对象。结果持久化的单一失败边界（`repository.prepare`、`storeOne`、`storeFailure`、`stageEvidence`、`stageOversize`、`attempts.settle` 的实际失败）统一抛 `MEASUREMENT_ATTEMPT_UNKNOWN`（原 cause 保留）且不写仓库；成功 settle 后按原始失败文本、`MEASUREMENT_RESULT_BYTES` 或 `MEASUREMENT_RESULT_INVALID` 报告，不被该边界改写。删除 `saved.evidence` 一律抛错与 `MEASUREMENT_ATTEMPT_PENDING`。

超硬上限的 observation 在 `ARTIFACT_LIMIT=67108864` 内完整 stage 为 evidence 并 settle `{ok:false,failure:{kind:'inline',value:'MEASUREMENT_RESULT_BYTES'},evidence,bytes}`；超过该上限时 `evidence=null`、`bytes='>67108864'` 如实披露，不虚称保存。

**读取上限反例（原生 JSON 实测）**：§4.4 原文的信封上限 `maxRecordBytes*4` 与 failure 上限 1MiB 同合法配置冲突。`attempts.maxRecordBytes=1024` 加 `inlineBytes=16384`（默认）时，三字段各 1400B 的合法观测生成 4102B 信封（大于 4096）；三字段各 16000B 时观测 48116B（≤`maxResultBytes` 49152）、信封 48142B。failure 文本 1048578 字符存为引用时 `rawBytes=1048580`（大于 1048576）。两者都会让已成功 settle 的 attempt 在重放改抛 budget，首次与重放不同事实。现合同把信封与 failure 引用读取统一到既有 `ARTIFACT_LIMIT`（即 `stageEvidence`/artifacts 的原生 `maxObjectBytes`），不新增 quota/config：成功 stage 且不超过该上限的值必须可读；超过该上限的值无法 stage，首轮即为 `MEASUREMENT_ATTEMPT_UNKNOWN`，下次 pending/unknown 同码。`MeasurementFailure('MEASUREMENT_ENVELOPE_BUDGET')` 仅保留为 artifact ref 元数据超过 64MiB 的完整性守卫。

**消费者 shape 变化**：completed outcome 的 value/original/source 与仓库 page 行为 StoredValue；完整正文经 `measurementRepository.readPayload` 分块读取。`pr10-runtime`、`pr10-runtime-activation` 读 `.value.value`；`pr10-query-boundaries` 在 append 前 `prepare`；`pr10-observation-boundaries` 读 `.original.value.body`。PR10-T04 的 attempt evidence 现在是 observation 本体，不再是 `{ok:true,observation}` 信封。

**行为变化**：Date 等带 toJSON 的值过去能被 stringify 并写入仓库，现在按测量合同报 `MEASUREMENT_RESULT_INVALID`；重放不再报 `MEASUREMENT_ATTEMPT_PENDING` 或 25 字节窗口的 `MEASUREMENT_RESULT_BYTES`，ledger 结果未知时按真实事实报 `MEASUREMENT_ATTEMPT_UNKNOWN`。

**T02 口径（主裁定）**：append 真失败一次时，首次 `runOne` 为 incomplete（事务真实回滚，仓库无行），但 observation 在 append 之前已 settle 到 attempts；第二次重放 completed 且不重新 observe。不能把事务失败记为 completed；“两次 completed”只适用于没有事务失败的配置。测试 T02 以 observe 计数、attempt settled 与空仓库行证明这三个事实。

**未引用对象**：`LocalArtifacts.collect(retained)` 只在 `packages/core/src/artifacts-local/store.ts:28` 定义，`packages/core/src/artifacts-local` 与 `packages/core/src/sqlite-history` 内无调用者（全包 grep 亦无）；完整点/GC 不回收事务前 stage 的未引用 artifact。登记为后续 GC 事项，本 PR 不扩实现。
