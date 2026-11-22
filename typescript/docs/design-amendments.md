# 设计补充（2026-10-29 修复轮）

本文件承载修复轮对 `inputs/society0_next_refactor_v3/docs/` 设计包的补充与修订文字。设计包本身只读；每节由对应簇在自己的 PR 中追加，标题格式 `## <finding-id>：<主题>`，正文首行写明"补充/修订 inputs/society0_next_refactor_v3/docs/<文件> §<节>"。

## 约定

- 各簇只追加本簇小节，不修改他簇小节。
- 需要裁决的事项与已裁决结果随对应实现记录保留。

## NEW-5：测试基线只验证当前构建

补充 inputs/society0_next_refactor_v3/docs/12-validation.md §一。

`tests/` 只允许导入当前 `.build`、node 内置模块、同目录支持文件或安装态 `@society0` 包。导入仓外冻结快照、源码树（`packages/*/src`、`packages/*/build`）、主机绝对路径与 env 兜底由 `scripts/check_tests.py` 拒绝；`scripts/test-build.py` 在运行前核验 `@society0/{host,core,schemastery,cordis}` 解析到 `.build` 内，且 build 层测试不含裸 `@society0/` 导入。

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
- 排队且未 BEGIN 的事务取消时，在拒绝返回前释放操作 ID；持有有效执行租约的调用方可立即用同一 ID 重试。旧任务出队时跳过 BEGIN，其物理容量和 permit 保留至 settled；旧任务的结算保留新重试的身份。已 BEGIN 的事务须等待 `transaction` 确定未提交，或 `probe` 返回 `not-committed` 后再重试。在途期间的 `OPERATION_EXISTS` 表示并发重复；已提交重复由持久回执判定。
- **重试会再次执行回调，回调必须幂等，或只包含同一事务内的 SQL。** 外部效果的结果写入 session 日志；恢复只回到完整点，完整点之后的外部调用会重新执行。
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

## RV3-08：Thread 大消息的精确字节预检与分块写入

补充 inputs/society0_next_refactor_v3/docs/15-next-lessons.md §2.2、docs/07-storage.md:76,112、docs/16-domain-design.md:37。

Thread 消息的字节预算覆盖整个 Message 对象的 JSON（包括原始 provider 字段），在 stringify 前用有界预检核对 UTF-8 字节：`prepareBoundedJson` 走 `jsonByteBound` 的迭代计数（与 `JSON.stringify` 字节数一致），超过 `maxMessageBytes` 时由 `Thread.append`/`Thread.appendInput` 以 `THREAD_MESSAGE_BYTES` 拒绝；顶层 undefined/function/symbol 不是可编码 Json，抛 `PAYLOAD_JSON_VALUE`（对象中同类属性跳过，数组中同类元素按 `null` 计数，保持原生 stringify 语义）。通过后用原生 `JSON.stringify` 生成字节，再经 `textChunks` 按 `artifacts.chunkBytes` 分块、可取消地 stage；`chunkBytes` 小于 6 时以 `PAYLOAD_CHUNK_BUDGET` 拒绝。同步 stringify 与预检不可抢占，峰值由 `maxMessageBytes` 约束；取消只在 stage 分块边界生效。`prepareValue` 语义不变，现有调用方不受影响。

## RV3-05：driver 错误的阶段分类与持久未知

补充 inputs/society0_next_refactor_v3/docs/18-inference-and-reuse.md §四（driver 部分）、prs/PR08.md。

`driver-llm` 的 activation 对全部抛错源只分类一次，统一为 `LlmActivationError{reason,code,stage,cause}`；已分类错误原样传递，cause 保留内层错误对象。reason 取 `InferenceStopReason` 并新增 `setup_error`、`perception_error`、`thread_error`、`internal_error`；stage 标识触发阶段（`db.snapshot`、`db.close`、`thread.open`、`thread.history|prepare|append|appendInput|consumer`、`prompts`、`context.freeze`、`artifact.stage`、`perception.collect|material|commit`、`actions.receipts`、`model.prepare`、`actor-tools`、`runtime.acquire`、`runtime.run`、`transaction:<label>`、`unclassified` 仅限未分类的内部不变量）。ActivationResult 的 incomplete evidence 增加 `code`、`stage`、`committed`、`receipt`；`error` 保留 cause 文本。

- 事务按 B §1.3a 的调用方处置表：`SQLITE_COMMIT_UNKNOWN`、`SQLITE_SEGMENT_FAILED` 为 `persistence_unknown`（SEGMENT_FAILED 带 `committed:true` 与真实 receipt，且该 operationId 不进入 requiredOperations；COMMIT_UNKNOWN 的 `committed`/`receipt` 为 null），`SQLITE_TX_OUTCOME_MISSING` 为 `internal_error`，其余确定未提交或 body 错误为 `persistence_error`。
- `view.close` 只在主体成功时上抛 `persistence_error/db.close`；已有主错误时 close 失败不覆盖主错误。
- 取消只在 signal 已中止时按同一 reason 对象、cause 链或原生 `AbortError` 名称识别；`persistence_unknown` 与 `context_limit` 优先于 `cancelled`。lease release 失败保留 `persistence_unknown`，其余改写为 `persistence_error`。
- `actor-tools` 的工具动作或回执保存以 `SQLITE_COMMIT_UNKNOWN`/`SQLITE_SEGMENT_FAILED` 失败时上抛 `INFERENCE_PERSISTENCE_UNKNOWN`，经 runtime 得到 `persistence_unknown`。失败回执的 value 增加 `persistenceUnknown`。


## RV3-06：大访谈结果与测量记录

补充 inputs/society0_next_refactor_v3/docs/07-storage.md:112、prs/PR10.md:15、PR10-T04。

Social 测量合同改为 `MeasurementObservation{value,original,source:Json}` 与 `PreparedMeasurement{value,original,source:StoredValue}`，`MeasurementRecord extends PreparedMeasurement`；`StoredValue` 直接使用 `@society0/core/contracts/data` 的导出，DDL 不变，不保留旧 shape 或迁移。

`measurementRepository` 新增 `inlineBytes`（`Schema.natural().min(1).max(16384).default(16384)`，`new-run`/`affectsResults`）、`prepare`（逐字段调用现有 `prepareValue`）与 `readPayload`（`readValue`）。`append` 要求字段已是 StoredValue，内联字段超过 `inlineBytes` 时以 `MEASUREMENT_PREPARE_REQUIRED` 拒绝；同一事务内 `attachValue` 三字段后写入 JSON，`page` 按 StoredValue 解析。`measurements` 在 observe 后先用 `jsonByteBound` 检查：超过 `maxResultBytes` 得 `MEASUREMENT_RESULT_BYTES`，非 Json（toJSON/Date、BigInt、循环）得 `MEASUREMENT_RESULT_INVALID`；非循环共享引用按原生 JSON 展开语义计数，不误判超限。

访谈结果写入该主体线程的 session 日志：观察成功时在检查之前写 `measurement` 事件（含完整 observation），失败时写 `attempt/failed`（`cause` 为 `String(error)`，保留 `original`）。超过 `maxResultBytes` 的 observation 同样完整记录，再报 `MEASUREMENT_RESULT_BYTES`。重新采集会重新观察，不从日志短路。

**消费者 shape 变化**：completed outcome 的 value/original/source 与仓库 page 行为 StoredValue；完整正文经 `measurementRepository.readPayload` 分块读取。`pr10-runtime`、`pr10-runtime-activation` 读 `.value.value`；`pr10-query-boundaries` 在 append 前 `prepare`；`pr10-observation-boundaries` 读 `.original.value.body`。

**行为变化**：Date 等带 toJSON 的值过去能被 stringify 并写入仓库，现在按测量合同报 `MEASUREMENT_RESULT_INVALID`。

**未引用对象**：`LocalArtifacts.collect(retained)` 只在 `packages/core/src/artifacts-local/store.ts:28` 定义，`packages/core/src/artifacts-local` 与 `packages/core/src/sqlite-history` 内无调用者（全包 grep 亦无）；完整点/GC 不回收事务前 stage 的未引用 artifact。登记为后续 GC 事项，本 PR 不扩实现。


## NEW-1：JS 排序与游标统一为码点序

补充 inputs/society0_next_refactor_v3/docs/10-builtin-plugins.md 的 slot-strategies 表、docs/16-domain-design.md §4.2、docs/07-storage.md §50。

`@society0/host/order` 导出 `compareCodePoints(a,b)`：以 UTF-16 码元折叠为 Unicode 码点序，不读取 ICU/locale。`host` 不依赖 `core`，`core`、`extras`、`social` 以 peer 方式引用该子路径；`build_entries` 从 `package.json` 的 `./order` 导出自动纳入编译，`sidecars.mjs` 跳过无 metadata/api 的导出，插件数不变。

JS 侧的排序与 keyset 游标全部改用该函数：`mount-context` 的 context 文件页、`slot-strategies` 的 tie-break、`social-ranking` 的平局 id、`social-recommendations` 的 owner、`social-repository` 的 owner/presentation versions、`social/registry` 的 dependencies，以及 `information`、`actions`、`exploration/root`、`mount-world`、`perception/selection`。SQLite 原生 `ORDER BY` 保持 BINARY；`addon.cc` 以 UTF-8 绑定文本且全仓无 `COLLATE`，故良构字符串下三者同序。

设计中的 slot-strategies `tieBreak` 不新增 Config：只有 `id-codepoint` 一个取值，由 `@society0/host/order` 固定，catalog 登记为 dropped。排序前提是良构 UTF-16；孤立代理由 `sqlite-native` 入口的 NEW-6 检查拒绝（`SQLITE_TEXT_NOT_WELL_FORMED`），比较函数本身不对孤立代理作合同承诺。


## RV3-14：workspace rename 源父版本（撤回，保留特征测试）

补充 inputs/society0_next_refactor_v3/docs/10-builtin-plugins.md 的 workspace 条目、docs/17-actor-foundations.md §1.2。

RV3-14 撤回：`extras/src/workspace/index.ts` 的 `mutate` 在 rename/create 后无条件 `await this.touch(tx, scope, parent.path)`，`parent` 取自 `mutationParent(from)`，即源父目录；`renameNode` 在目标父与源父不同时额外 touch 目标父。跨目录 rename 两个父各 +1，同目录 rename 只 +1，`/workspace` 祖先不增。生产源码无需修改。

保留 `tests/pr09-workspace-rename.test.mjs` / `RV3-14-T01` 作为特征测试，锁定源父 touch 的唯一语义：跨目录 a/x→b/x、同目录 b/x→b/y、目录子树 b/d→a/d 的版本、objectId、可见性与 list 结果。mutation 只删除编译副本 `workspace/index.js` 的 mutate 末尾源父 touch，跨目录 `a0+1` 断言真实失败；恢复字节后 green。该测试不宣称修复生产 bug，也不新增 workspace 模块 quality scope。


## NEW-D1：插件 Config 未知键的 host 单点严格检查

补充 inputs/society0_next_refactor_v3/docs/04-configuration.md 的未知字段合同。

host 在 profile resolver 之后的 `resolution.value`、Schema validate 之前，以及 `createHost` boot 的 `ctx.plugin` 之前，统一按实际 Schema 结构检查未知键：
- `packages/host/src/config-resolvers/strict.ts` 的 `unknownConfigPaths` 递归 object（仅声明键）、array、tuple（额外元素）、dict（键开放、值递归）、union（取能通过且未知键最少的分支）、intersect（声明键组合）与 wrapper inner；`any`/const/原始类型开放。
- 错误统一 `UNKNOWN_CONFIG_FIELD`，带 `entryId`/specifier、JSON Pointer `paths`；错误信息首路径。
- profile 检查落在 `profile-loader/effective.ts` 的 `validateConfig`；boot 检查在 `bootstrap.ts` 的 `boot` 循环实际 `ctx.plugin` 前。Cordis `ctx.plugin` 合同本身不变，文档只描述 host 入口的额外检查。

实际 fork 的 `Schema.resolve` 在 strict 模式只移除顶层未知键，不递归清除嵌套对象未知键；本检查是 host 层递归合同，不把 strict 结果当嵌套 oracle。`dict`/`any` 的开放键保持不变。

证据：`S1` 覆盖 top/topology/prompts 嵌套、model-pi array(any)、social union 正值 -1/10、createHost boot；`S2` 覆盖结构正反与 pointer 转义；`S3` 4 个 shipped profile 全部编译；`S5` 源码启发式守卫见 [tests/config-consumers.test.mjs](../tests/config-consumers.test.mjs)，9 个整对象/3 个 const 例外与 missing-root 反例均在测试中现状。


## RV3-17：replay.mode 删除（重演由入口装配）

`core/src/replay` 是读取服务（[replay/index.ts](../packages/core/src/replay/index.ts)）：`lookup`/`compare`/`readArtifact` 读取既有 attempt 证据，不拦截外部副作用。恢复、确定性重执行和真实重跑由入口装配选择；profile 通过 [model-replay](../packages/core/src/model-replay/index.ts) 或 [model-pi](../packages/core/src/model-pi/index.ts) 选择模型路径，replay 插件本身不选择 record/replay/verify。

删除 `Config.mode` 及 `/mode` configPolicy，`Config` 只剩常量合同 `onMissingExternalResult: const('fail')`，policy 只余 `/onMissingExternalResult`；`tests/pr11-support.mjs` 的 replay entry 改为 `{}`。旧作者配置 `mode:'replay'` 由 NEW-D1 报 `UNKNOWN_CONFIG_FIELD replay/mode`。生成 metadata 由 Core build 重新写出，不手改 JSON。公开操作指引见 [public-usage 重演](public-usage.md#重演)。


## RV3-09：B05 preview/fullRef 与 B02–B05 legacyScope

`social-perception.previewChars` 默认 `-1`，非负时只对 post 感知的展示正文按 Unicode 码点截短；超限加 `truncated:true` 与 `fullRef:{information:'social/posts',input:{id}}`，原始信息经 public `social/posts` 的可选 `{id}` 输入读取，SQL 加 `p.id=?`（`id` 为可选字符串，合法空串同样按精确 id 过滤；未提供才按 authorId/scope），保留既有 authorId/cursor/byte budget/BINARY 语义。information source 对 `social/posts` 声明 `properties.id` 与 `properties.authorId`。排名与测量继续读完整正文。

用户裁决：B02/B03/B04 选 (b) 如实未承接；B05 选 (a) 承接。四份 `legacyScope-B02..B05.json` 记录准确 carried/notCarried；本文件承载稳定合同。


## NEW-6：模型派生标识与失败原因文本

Core 使用原生 `String.isWellFormed()` 检查即将参与持久化标识的文本。[Thread](../packages/core/src/thread/index.ts) 在查询前检查 `actorId`、`momentId`，在编码或暂存消息前检查 `message.id`，拒绝时报告 `THREAD_TEXT_NOT_WELL_FORMED` 并带 `field`。[ActorTools](../packages/core/src/actor-tools/binding.ts) 在拼接 operationId 前检查模型的 `toolCall.id`，拒绝时报告 `INFERENCE_MODEL_TEXT_NOT_WELL_FORMED`，推理结局归为 `provider_error`。标识保持原始值，调用方应修正其生成源。

失败原因属于诊断文本：[ActionCall](../packages/core/src/actions/execution.ts) 向 session 日志写入 `attempt/failed` 时，`cause` 使用原生 `toWellFormed()`，原始文本保留在 `message` 字段，以 JSON 的代理码元转义形式保存，读取后可恢复原始字符串。

结局优先级保留持久化未知、持久化失败这些已发生事实，再考虑模型文本错误。通用 `SQLITE_TEXT_NOT_WELL_FORMED` 沿用存储阶段分类；模型错误分类依赖明确的模型入口错误码。

## CR-02：批准的配置缺口

以下差异按批准范围保留，并在[配置对账](config-reconciliation.json)登记。`deferred` 表示延期实现；`dropped` 表示本轮接口已取消，当前行为及其限制见表；`renamed` 同时记录改名与语义差异。

| 计划字段 | 处理 | 当前行为与限制 | 后续 |
|---|---|---|---|
| `host/tasks/shutdownTimeoutMs` | `implemented` | 默认30000ms；物理任务不响应abort时按上限报告未结束任务，关闭保持halted，之后继续跟踪真实结束 | 已实现 |
| `core/run-controller/pauseTimeoutMs` | `implemented` | 默认30000ms；超时halt执行栅栏并报RUN_PAUSE_TIMEOUT，不返回安全边界 | 已实现 |
| `core/proposal-events/leaseTimeoutMs` | `implemented` | 默认60000ms；reservation到期自动回收并唤醒等待者，显式release/ack仍可用 | 已实现 |
| `core/termination/maxAdvances` | `deferred` | 无推进次数上限 | 后续排期另定 |
| `host/slots/maxDepth` | `deferred` | 嵌套slot无深度上限 | 后续排期另定 |
| `core/run-controller/onUnknownCommit` | `dropped` | 设计要求唯一取值 `halt`；运行时对提案异常固定 `reservation.cancel` 后 `fence.halt`，未知提交按 `persistence_unknown` 停机，字段没有第二个取值 | 无（唯一取值；改变停机语义需新设计） |
| `host/scaffold/language` | `deferred` | JS+JSDoc模板未支持 | 后续排期另定 |

`extras/shell/timeoutMs`、`core/exploration-shell/maxCommandCount`、`host/diagnostics/historyLimit`、`core/driver-llm/maxTurns`、`core/termination/onIncomplete`、`core/prompts/maxContextBytes`、`extras/vector-index/maxLag` 已按设计补回并有真实超限/超时测试，不再登记为缺口。`host/diagnostics/level` 与 `core/prompts/sectionOrder` 也按设计表恢复为可配置字段。

`LocalArtifacts.collect` 已删除：设计 `catalog/plan.json` 的 ArtifactStore 操作只有 `stage/read/localFile/close`，设计包 `docs/07-storage.md` §4.3 把对象删除归显式 retention/GC 操作，仓库内没有调用入口。对象 GC 仍待独立维护操作实现，不在本插件公开合同中。

其他已知限制按原条目保留：`core/sqlite-history/maxBufferedBytes` 的统一跨阶段在途预算、`host/profile-loader/requiredReadyTimeoutMs` 的 required 服务就绪期限，以及 Codex 真实订阅验证。对应范围和原有处置继续以配置对账及验收记录为准。

## K3：外部调用记录改为 session 日志

修订 RV3-04、NEW-3、RV3-06 中 attempts 账本相关的文字（这些小节已删除或改写）。

- 每个 session（主体 × 激活线程）一个只追加 JSONL，位于 run 目录 `sessions/`。首行 `{type:'session',version:1,actorId,momentId,createdAt}`，之后每行 `{type,seq,time,data}`。非线程调用（embedding、Codex 凭据刷新）写入 `@system` 的固定 session。
- 模型请求：header 变化才写 `request/header`；上下文是上次的延续时只写 `context/append`，否则写 `context/reset`；随后写 `request`（含 `contextLen`、`headerSeq`）。结论写 `response`（含 zstd+base64 的原始回包）或 `attempt/failed`。失败按退避重试至 `maxRetries`，4xx 中只重试 408/429。
- 事件在内存攒批，按 `flushMs` 或发请求前的 barrier 写给操作系统，不 fsync；发布完整点时 `heads()` fsync 并把各文件长度写进点记录，恢复时 `truncate(heads)` 截回该长度。只从完整点恢复，完整点之后的外部调用会重新执行。
- 分叉目标在 `sessions/parent.json` 记录源 run 与截断长度，读取时先读源前缀，写只追加自己的文件。
- `replay.lookup({session,operationId,attempt})` 从日志读该尝试的结论事件，没有记录报 `REPLAY_MISSING`。
- 删除 `transport_unknown` 与外部结果未知的概念，以及 `REOPEN_AUTHORITY_UNAVAILABLE`（改为 `REOPEN_STORAGE_UNAVAILABLE`）。
