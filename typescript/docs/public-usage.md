# 公开 SDK 使用

本页说明独立安装用户如何用公开包加载 profile、创建 SQLite run、推进并重开历史。使用所选发行候选的 tarball 闭包；候选验收范围读取该候选随附的消费者结果。

## 一、安装

准备一个空目录，以及发行候选提供的公开依赖 tarball、[运行例子](../examples/run-profile.mjs)、[锁定工具](../examples/profile-lock.mjs)和[profiles](../profiles/)。Node 版本要求读取发行包 `engines.node`。下面的离线安装命令要求第三方依赖已在 npm 缓存中；首次联网安装可去掉 `--offline`。

```sh
mkdir public-consumer
cd public-consumer
npm init -y
# RELEASE 指向发行候选提供的公开 tarball 闭包目录。
npm install --offline --ignore-scripts "$RELEASE"/*.tgz
# 把 run-profile.mjs、run-profile-branch.mjs、profile-lock.mjs 和 profiles/ 复制到本目录。
node profile-lock.mjs module-lock.json @society0/host @society0/core @society0/extras @society0/social
node run-profile.mjs --help
```

原生二进制由发行包携带；平台和架构须与候选证据一致。[profile-lock.mjs](../examples/profile-lock.mjs) 从已安装包的公开 metadata export 记录版本、源身份和 patch 顺序。保存生成的锁和安装 lockfile，后续加载使用同一候选。

## 二、运行

先检查有效配置，再创建独立 run。标准输出是一份 JSON；失败信息写入标准错误并以非零状态退出。

```sh
node run-profile.mjs --profile profiles/rule-sqlite.json \
  --lock module-lock.json --directory runs/rule --run-id rule --inspect
node run-profile.mjs --profile profiles/rule-sqlite.json \
  --lock module-lock.json --directory runs/rule --run-id rule
node run-profile.mjs --profile profiles/rule-sqlite.json \
  --lock module-lock.json --directory runs/rule --run-id rule --reopen
```

`--inspect` 经过 ProfileLoader 的真实配置解析和静态依赖检查，输出来源与默认值。执行模式通过 RunDirectory 拥有 SQLite、历史和原生 slots；正常退出前释放借用、关闭 run 并等待宿主资源关闭。重开使用已发布完整点，跳过初始化 seed。`--offline` 会真实挂载 provider 与原生 slots、打开根完整点并观察，跳过业务推进和模型凭据读取；结果明确标为 `offline-mounted`。

- [rule-sqlite](../profiles/rule-sqlite.json)：阶段调度、主体感知和 passive 规则基线。
- [event-sqlite](../profiles/event-sqlite.json)：真实事件源与事件消费确认，例子提交一个事件并关闭输入。
- [social-sqlite](../profiles/social-sqlite.json)：Social 主体、关系、传播、感知与推荐，使用 `driver-llm`、`model-pi` 和 `agent-runtime-pi`；显式选择本机 Qwen 网关。
- [social-codex-sqlite](../profiles/social-codex-sqlite.json)：同一 LLM driver 与 pi runtime，provider 切换为 `model-codex-subscription`；默认关闭账号授权。

结果包含完整点、观察页、历史计数、耗时、RSS/heap 与关闭报告。`advanceAndSaveMs` 是推进与保存的合计时间；`history.timings` 记录实际 JSON 编码、文件发布 IO、压缩/解压流水线与 publication/restore 等计时。嵌套阶段会重叠，应分别报告；压缩流水线包含流 IO，native 阶段包含 worker 启停。纯压缩 CPU、独立流读写和设备 IO 时延保持 `unknown`。

## 三、覆盖与故障

`--patches <patch-array.json>` 接收 ProfileLoader 的 CLI 层 patch 数组。patch 的 `config` 替换该入口的整个配置对象；覆盖时保留所需必填字段。例如新 run 的规则标签覆盖：

```json
[{"id":"rule-passive","config":{"resultTag":"experiment-a"}}]
```

配置字段和更新策略以安装包 `Config`、`plugin.meta.json` 与 `--inspect` 的有效配置为准。会影响结果的配置变化使用新 run／显式分支；保留原 run 的锁和 profile。

profile 的生效配置和 `createHost` 的 boot 配置在 Schema validate 前检查未知键；嵌套 object 也递归拒绝，`dict` 的键开放但每个值按 `inner` 继续检查，`any` 与原始类型分支开放。错误码为 `UNKNOWN_CONFIG_FIELD`，带入口标识（`entryId` 或 boot specifier）、首条 JSON Pointer 以及完整 `paths` 列表；按该路径修正或删除误键。具体结构规则、union/intersect 选择与 `direct Cordis ctx.plugin` 不在 host 合同内的边界见 [NEW-D1：插件 Config 未知键的 host 单点严格检查](design-amendments.md#new-d1插件-config-未知键的-host-单点严格检查)。

遇到 `SOURCE_LOCK` 或缺少 export，检查该候选是否包含所选 profile 的完整公开入口，并重新生成与该安装一致的锁。遇到 `BOOTSTRAP_UNKNOWN`、`HISTORY_UNKNOWN` 或关闭失败，保存完整错误、run 目录及 operationId；通过对应 bootstrap/history/branch 的 `probe` 查询操作结果，依完整点恢复。已有可验证完整点时，可用 `--reopen --observe` 读取源完整点、live revision 与 clean/dirty/unknown 状态，保持业务推进关闭。对 unknown 操作重新发送前先完成事实核实。

Codex 订阅使用独立 provider 与 pi runtime。真实账号登录需要额外授权；离线配置检查与真实订阅调用分别记录。该例子的订阅配置执行门返回 `CODEX_NOT_AUTHORIZED`，`--inspect` 核对配置；`--offline` 验证原生装配和根点消费，真实订阅门仍需单独验收。

## 四、恢复

新建要求 world 与 lineage 目录尚未使用，创建命令重复运行会在挂载数据库之前失败。默认 authority 位于 world 同级的 `lineages/<world目录名>`，identity 为 `--run-id`；结果输出实际 `lineage` 引用。可通过 `--lineage-directory` 和 `--lineage` 指定外部 authority。分叉及其 reopen 保持源 lineage 的目录与 identity，源 world 删除后的证据保留以该独立 authority 为基础。`--reopen` 遇到缺失 authority 会报告 unavailable，保留 unknown；该路径不执行 initialize。

## 五、模型调用方

两个模型 profile 可用 `--offline` 检查完整的原生装配和根点。Social 的真实请求由获授权调用方通过导出的 `runProfile` 接入内存凭据；默认命令行返回 `MODEL_NOT_AUTHORIZED`。凭据引用为 `authorized-local`，实际 provider ID 为 `local-gateway`；驱动选择注册名 `pi/v1`。每次 activation 的模型请求与工具调用预算读取 profile 中 `driver-llm` 的 `maxRequests`、`maxToolCalls`，并通过安装包 `Config` 和 `--inspect` 核对有效值；完成要求为实际 `social.publish` 回执。

```js
import {runProfile} from './run-profile.mjs';
import {InMemoryCredentialStore} from '@earendil-works/pi-ai';

export async function runAuthorizedSocial(options, gatewayKey) {
  return runProfile({...options, allowModelRequests: true, configureHost: async ctx => {
    const owner = await ctx.plugin({inject: ['modelCredentials'], async apply(ctx) {
      const store = new InMemoryCredentialStore();
      await store.modify('local-gateway', () => ({type: 'api_key', key: gatewayKey}));
      ctx.modelCredentials.register('authorized-local', store);
    }});
    await owner.await();
  }});
}
```

该凭据 owner 归属宿主，例子在 run 关闭后释放宿主。候选证据分别报告离线装配、获授权 Social 请求和真实订阅验收；请求次数以实际 attempt 证据核对。

## 六、分叉

[分支例子](../examples/run-profile-branch.mjs) 使用 RunBranch 的公开 plan/create/probe 协议，目标沿用源 SQL schema。复制一份目标 profile，在 `patches` 末尾追加例如 `{"id":"checkpoint-policy","config":{"every":2}}`，即可声明不同策略；目录、run identity、generation 与共享 lineage 由例子补入原生 patch。

```sh
node run-profile-branch.mjs --profile profiles/rule-sqlite.json \
  --directory runs/rule --run-id rule --lock module-lock.json \
  --target-profile rule-fork.json --target-directory runs/rule-b --target-run-id rule-b
node run-profile.mjs --profile runs/rule-b/profile.json \
  --directory runs/rule-b --run-id rule-b --lock module-lock.json \
  --lineage-directory runs/lineages/rule --lineage rule --reopen --observe
```

分支默认观察源与目标，不推进目标。`--advance-target` 显式推进零模型目标；获授权 Social 调用方可导入 `forkProfile`，传入同样的 `configureHost`、`allowModelRequests` 以及 `advanceTarget:true`。结果保留分支 operationId、源/目标完整点、目标有效配置、实际 authority 与关闭状态；目标保存自己的完整 profile，后续 reopen 继续使用所输出的共享 lineage。

分支可用 `--point-id <complete-point-id>` 选择历史完整点。`--diagnostics <new-ndjson-file>` 在创建前记录源 history baseline，此后每5秒串行记录计数/阶段耗时差值、active、elapsed、RSS与采样开销；结束前记录末次快照。当前点备份与历史段重放分别计量，阶段累计时长存在包含关系。发生创建异常时，例子输出 operationId 和目标目录，供公开 `runBranch.probe(operationId)` 核实。

模型profile同时声明 `/world`、`/results`、`/workspace`、`/context`。Workspace的公开migration在bootstrap采用实际Extras来源；公开 `workspaceInitializer({after:[actorSeedId]})` 登记于bootstrap的原生consumer，随该scope一起关闭，reopen沿用已有数据。测试中的 `model-replay` 用于零网络的原生PI/工具与冻结context验证；Social默认provider仍为本机网关。

Event目标使用 `--advance-target --target-events target-events.json`，或传 `targetEvents` 数组给 `forkProfile`。批次需包含新事件ID，offset严格大于选定完整点并递增，time沿持久moment非递减；例子向目标原生generation source推入该有限批次后调用end。源事件与目标事件分别提供。源示例完成offset/time=1后，目标文件可为：

```json
[{"id":"target-event-2","offset":"2","time":"2","payload":{"handler":"plain.update","actorId":"alice","attributes":{"name":"Alice","message":"target event only"}}}]
```

## 测量结果

`measurements` 的 completed outcome 与 `measurementRepository.page` 行的 value/original/source 是 StoredValue（`{kind:'inline',value}` 或 `{kind:'reference',ref}`）。引用字段用 `measurementRepository.readPayload(ref,{offset,length,maxBytes})` 读取完整正文；分页响应只含引用元数据。超过 `maxResultBytes` 的回答以 attempt 证据保存并使测量 incomplete；失败、超限与未知分别报原始失败文本、`MEASUREMENT_RESULT_BYTES` 与 `MEASUREMENT_ATTEMPT_UNKNOWN`，重放不重新观察。

## 驱动错误分类

`driver-llm` 的 `activate` 失败时返回 `status:"incomplete"`，`value.reason` 取推理原因或调用阶段原因：`setup_error`、`perception_error`、`thread_error`、`internal_error`，以及既有 `provider_error`、`context_limit`、`cancelled`、`persistence_error`、`persistence_unknown`、`transport_unknown`。`evidence` 同时带 `code`、`stage`、`committed`、`receipt` 与 cause 文本；`stage` 指出失败调用（如 `thread.appendInput`、`transaction:input`、`actions.receipts`）。

调用方按 `persistence_unknown` 处理存储提交未知：停止对该 operationId 的写入，用 `probe` 核实后再决定恢复或 fork；`SQLITE_SEGMENT_FAILED` 的 `evidence.committed` 为 `true` 且 `evidence.receipt` 为真实提交回执，但该 operationId 不会出现在 `requiredOperations`。取消只在 signal 已中止且错误链命中同一 reason 对象或原生 `AbortError` 时为 `cancelled`，存储未知与 `context_limit` 不会被取消覆盖。工具路径的存储未知经 `INFERENCE_PERSISTENCE_UNKNOWN` 报为 `persistence_unknown`；外部动作结果未知（`ACTION_ATTEMPT_UNKNOWN`，无 storage 标志）仍为 `transport_unknown`。工具调用前的 ledger claim 若返回 unknown 或已有未结算记录，驱动报 `persistence_error/attempts.undispatched`（`ATTEMPT_LEDGER_UNKNOWN`/`ATTEMPT_LEDGER_INCOMPLETE`），不伪造 not_dispatched 结算；已 settled 的记录保留不重写。行动 claim 返回 `ACTION_ATTEMPT_UNKNOWN{storage:true}` 属存储不确定，报 `persistence_unknown`（回执 `persistenceUnknown:true`、`externalUnknown:false`）。

## 事务结果合同

`database.transaction` 成功 resolve 表示事务已提交；reject 的对象表示事务确定未提交，或结果未知：

| 结果 | 含义 | 调用方处置 |
|---|---|---|
| resolve | 已提交 | 使用 receipt |
| 原始 `signal.reason` / `AbortError` | BEGIN 前取消，或 BEGIN 后回滚 | 按取消处理；等 ID 退出在途后重试 |
| `SQLITE_COMMIT_UNKNOWN` | COMMIT 已发出但结果未知 | 停止写入；worker 退出后 probe；不重放回调 |
| `SQLITE_SEGMENT_FAILED` | 已提交，但增量段证据缺失 | 停止写入；从完整点 fork 恢复；receipt 见 `cause.receipt` |
| `SQLITE_TEXT_NOT_WELL_FORMED` | 事务内文本含孤立代理，已回滚 | 明确失败；领域入口校验原始文本 |
| 其他 `SQLITE_*` / body 错误 | 确定未提交 | 原样处理或按 body 阶段分类 |

取消按对象身份（`error===signal.reason`）或 `name==='AbortError'` 识别。完整设计说明见 `docs/design-amendments.md` 的 B 节。


## 排序与游标

所有 JS 侧排序和 keyset 分页统一使用 `@society0/host/order` 的 `compareCodePoints(a,b)`：按 Unicode 码点比较，与 SQLite `ORDER BY` 的 BINARY（UTF-8 字节序）一致，不读取 ICU 或 locale。context、information、action、exploration、social ranking/recommendations/repository 的排序与游标使用同一规则；分页游标必须与排序使用同一个比较函数。

前提是输入为良构 UTF-16。`sqlite-native` 在文本入口拒绝孤立代理并报 `SQLITE_TEXT_NOT_WELL_FORMED`，因此从数据库读回的文本与排序前提一致。孤立代理不属于本比较函数的合同范围；其码元行为仅为观测记录。


## 重演

按运行意图装配入口：读取既有外部结果时挂载 `@society0/core/replay` 与目标记录/历史入口；真实重跑选择对应 provider/profile 入口，配置中不写 `record`/`replay`/`verify` 枚举。旧写法会被 host 拒绝为 `UNKNOWN_CONFIG_FIELD replay/mode`。操作与合同细节见 [RV3-17](design-amendments.md#rv3-17replaymode-删除重演由入口装配)。
