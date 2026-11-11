# 运行与 CLI

这份 reference 只讲当前 `society0` CLI 的真实入口：检查 profile、启动 run、查看状态、读取结果、恢复运行，以及相关的目录、锁文件和种子配置。分叉不在这里，见 [causal-branches.md](causal-branches.md)。

## 一、安装与命令形式

CLI 来自公开包 `@society0/cli`，可执行名是 `society0`。Node 版本要求读取发行包的 `engines.node`。如果命令不在 `PATH` 中，可以直接调用构建产物：

```sh
node <安装目录>/cli/dist/main.js <command> [options]
```

下文统一写成 `society0 <command>`。所有平台命令支持 `--json`，成功时输出带 `schema` 的 JSON，失败时把 `society0.error/1` 写到标准错误并以非零状态退出。

## 二、profile 与锁文件

profile 是 `society-profile/v2` JSON，通常包含：

- `format`、`bundles`、`patches`、`configSources`。
- `lockfile`：模块锁文件的位置，相对 profile 或绝对路径。
- 一组按生命周期分组的插件条目：`run`、`bootstrap`、`generation`，有时还有 `workspace`。

公开 profile：

- `profiles/rule-sqlite.json`：规则基线，`plain`、`proposal-sequence`、`driver-rule`、`rule-passive`，不接模型。
- `profiles/event-sqlite.json`：外部事件驱动，`proposal-events`、`runtime-events`、`eventHandlers`，适合事件源研究。
- `profiles/social-sqlite.json`：Social 主体、关系、帖子、传播、感知、推荐和测量，装配 `driver-llm`、`model-pi` 和 `agent-runtime-pi`。
- `profiles/social-codex-sqlite.json`：同一 LLM driver 和运行时，提供方不同；默认需要额外授权。

从最接近研究机制的 profile 复制一份，不要从空文件拼整套装配。

### 生成模块锁

公开安装包通常附带 `examples/profile-lock.mjs`。它从已安装包的公开 metadata 记录版本、来源提交、patches 和 host ABI：

```sh
node examples/profile-lock.mjs module-lock.json \
  @society0/host @society0/core @society0/extras @society0/social
```

生成的锁默认拒绝 dirty 包；开发时可以显式传 `--allow-dirty`，所选状态会写进锁。后续加载使用同一份锁。

本地插件开发可以用 CLI 的 `lock` 命令：

```sh
society0 lock --base <实验目录> --out <实验目录>/lock.json \
  <本地包名> @society0/host @society0/core @society0/extras @society0/social --json
```

`lock` 从 `--base` 的 `node_modules` 按包名找包，读取版本、git 提交和 patches。包不在 git 仓库、没有提交或找不到时报 `LOCK_GIT` 或 `PACKAGE_MISSING`。

## 三、check

```sh
society0 check --profile <profile.json> [--lock <lock.json>] [--patches <patch.json>] \
  [--world <world.json>] [--base <实验目录>] [--credentials-env <ENV_NAME>] --json
```

`check` 只做静态装配：

- 解析锁文件，导入插件 metadata，校验配置 schema。
- 检查服务、槽位、凭据名和 migration 依赖。
- 不挂载插件、不连接模型、不创建 run 目录。

输出：

```json
{
  "schema": "society0.check/1",
  "ok": true,
  "missing": [],
  "warnings": [],
  "resolved": {"plugins": [], "slots": {}},
  "migrations": []
}
```

`ok=true` 退出 0；`ok=false` 退出 2；检查本身无法完成时输出 `society0.error/1` 并退出 1。

常见 `missing.kind`：

- `plugin`：找不到模块绑定，或宿主 ABI / 作用域不合法。
- `service`：服务没有提供者、提供者作用域倒置或重复提供。
- `config`：配置 schema 不合法或出现未知字段。
- `slot`：请求的 driver 或运行期槽位没有活动条目提供。
- `credential`：模型插件引用的凭据名没有注册。
- `migration`：migration 的 `after` 依赖不在本次装配集合里。
- `actor`：多个插件会写主体表，或 initializer 没有声明主体依赖。
- `world`：带 `--world` 时，根创建违反表约束或主体缺失。

`check` 通过不代表业务一定正确；它不推进 tick，也不验证运行期才出现的约束。

## 四、run start

```sh
society0 run start --profile <profile.json> --dir <run-dir> \
  [--lineage <lineage-dir>] [--ticks N] [--world <world.json>] \
  [--base <实验目录>] [--credentials-env <ENV_NAME>] \
  [--lock <lock.json>] [--patches <patch.json>] --detach --json
```

- `--dir` 必须是新目录。
- 默认 lineage 是 `<run-dir 的父目录>/lineages/<run-dir 的 basename>`，必须不存在。
- `--ticks N` 写入 `run-controller.maxTicks`，是上限。跑满 N 个 tick 后在已发布的完整点停下；来源先耗尽时提前结束。
- `--world` 是 JSON 对象，`actors` 段每条为 `{id, attributes, source}`，`source` 必填。
- `--base` 指定插件解析目录，从该目录的 `node_modules` 按包名查找。
- `--credentials-env` 只把环境变量名写进状态文件；变量值是 JSON，只在当前进程内存里注册，不写 run 目录，也不打印。
- 不带 `--detach` 时在当前进程运行到结束，再输出一次 status，适合本地调试。

启动前会重复一次 `check`；不通过时返回 `CHECK_FAILED`，不会留下 run 目录。

输出：

```json
{"schema":"society0.run/1","runId":"run-dir-name","pid":12345,"dir":"/abs/run","state":"running"}
```

后台日志写到同级 `<run-dir>.log`。

### 覆盖配置

`--patches` 接收 `EntryPatch[]`。每个元素的 `config` 替换该入口的整个配置对象，所以覆盖时要把需要的字段写全：

```json
[{"id":"random","config":{"seed":"study-2"}}]
```

会影响结果的配置变化要用新的 profile 和新的 run，不要改已有 run 的配置。

## 五、status

```sh
society0 run status --dir <run-dir> --json
```

只读 run 目录里的 `entry-status.json` 和 pid，不连接正在运行的进程。

```json
{
  "schema": "society0.run-status/1",
  "runId": "run-dir-name",
  "dir": "/abs/run",
  "state": "running",
  "completedTicks": 2,
  "frontier": {
    "codec": "staged/1",
    "value": "2",
    "logicalTime": "2",
    "completedCount": "2",
    "pendingAcknowledgements": []
  },
  "lastReason": null,
  "pid": 12345,
  "updatedAt": "2026-11-09T08:00:00.000Z"
}
```

状态：

- `running`：状态文件记为 running，pid 仍存活。
- `paused`：进程主动暂停，或状态仍是 running 但 pid 已消失；后者 `lastReason` 常为 `process-exited-without-status`。
- `completed`：引擎返回完成；`lastReason` 可能是 `sequence exhausted`、`round-robin exhausted` 或 `max ticks reached (N)`。
- `failed`：引擎返回失败或 worker 抛错；`lastReason` 是原始错误码或消息。

`completedTicks` 来自完整点 frontier 的 `completedCount`，运行中可能略落后于实际进度。

## 六、result

```sh
society0 run result --dir <run-dir> --json
```

读取 `entry-result.json`：

```json
{
  "schema": "society0.run-result/1",
  "runId": "run-dir-name",
  "dir": "/abs/run",
  "state": "completed",
  "result": {"status": "completed", "cut": {"frontier": {"completedCount": "2"}}},
  "activations": {
    "total": 2,
    "completed": 1,
    "failed": 1,
    "byReason": {"success": 1, "setup_error": 1},
    "limits": {},
    "failures": []
  }
}
```

`result` 是 run-controller 的原始结果。`activations` 是平台摘要：

- `total`：本次 run 记录的激活条数。
- `completed`：`requiredSatisfied=true` 的条数。
- `failed`：其余条数。
- `byReason`：按激活 `reason` 计数。
- `limits`：预算先耗尽的额度，键为 `requests`、`toolCalls` 或 `turns`。
- `failures`：未完成激活，最多 20 条，带主体、时点、原因、错误码、阶段和额度。

`run result` 只给运行级摘要和激活摘要，不提供任意业务表的查询。需要主体属性、公开表或测量表时，用宿主 `observation` 服务或发行包附带的观察脚本；不要把这个摘要当成完整数据导出。

## 七、resume

```sh
society0 run resume --dir <run-dir> [--credentials-env <ENV_NAME>] --detach --json
```

- 从 run 目录已发布的完整点继续。
- profile、锁、patches、ticks、lineage、base、world、credentials-env 都从 `entry-status.json` 读取。
- 进程仍存活时返回 `RUN_ALREADY_RUNNING`；已完成时返回 `RUN_COMPLETED`。
- 没有可验证的完整点或 authority 不可用时，报告不可用并保留现状，不会重新初始化。

因此，恢复的前提是 run 至少发布过一个完整点。进程在根创建完成前被杀掉时，可能没有 authority，这时不要反复 resume；先保留目录和错误，重建来源运行。

## 八、目录与身份

一个 run 目录通常包含：

```text
<run-dir>/
  entry-status.json        状态
  entry-result.json        完成后的结果
  bootstrap.json           根创建信息
  world.sqlite             世界数据库
  history/                 完整点和历史段
  artifacts/               产物
  metrics.jsonl            指标记录
  segments/                历史段
```

lineage 默认在同级：

```text
<run-dir 的父目录>/lineages/<run-dir 的 basename>/
  attempts.sqlite
  artifacts/
```

run 身份是 `--run-id`，默认取目录 basename。lineage 可以由 `--lineage` 指定。每次尝试使用新的 run 目录；不要复用旧目录来“续跑另一个条件”。

## 九、种子

`@society0/core/random` 的配置：

```json
{"algorithm":"philox4x32-10/v1","seed":"study-1"}
```

- `seed` 是字符串，默认 `study-1`。
- 同一 run 内，随机地址由运行、主体、操作和流标识共同决定。
- 改变 `seed` 会影响结果，属于新 run 配置。
- 公开 profile 示例使用 `public-example-1`，研究时应写清自己的种子。

种子只固定 Society0 的内部随机流，不固定 LLM 提供方的采样。

## 十、读有效配置

`check` 输出装配结果和缺失项，但不打印每个入口的合并配置。需要看有效配置时，可以用发行包附带的 `examples/run-profile.mjs --inspect`：

```sh
node examples/run-profile.mjs --profile <profile.json> \
  --directory <临时目录> --lock <lock.json> --inspect
```

`--inspect` 经过真实配置解析和静态依赖检查，输出来源与默认值，不打开 run，也不请求模型。不同发行包的示例参数可能略有差异，以 `--help` 为准。

## 十一、错误与现场

- `CHECK_FAILED`：启动前检查未通过，未创建 run。
- `RUN_ALREADY_RUNNING`：进程仍存活，先等待或停止。
- `RUN_COMPLETED`：run 已完成，不能再次 resume。
- `RUN_NOT_FOUND`：目录没有 `entry-status.json`，常见于指向了非 CLI 创建的目录。
- `RUN_NOT_COMPLETED`：run 还在运行或失败，没有可用结果。
- `RESULT_MISSING`：已完成但结果文件丢失。
- `REOPEN_AUTHORITY_UNAVAILABLE`：重开时找不到 world 或 attempts authority；保留现场，不要重新初始化。

失败时保留原始错误、run 目录和 operationId。分叉或恢复前先确认完整点，不要直接重发未知操作。
