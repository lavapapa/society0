# 平台入口（D5 草案）

平台（Society0 工作台）不 import Next 的内部模块，只通过 `society0` CLI 的带版本 JSON 交互。CLI 每次调用都是独立进程；状态和结果都落在 run 目录里，因此平台重启或 CLI 冷启动不影响已有 run。

约定：

- 平台、脚本和其他语言只能调用 `node_modules/.bin/society0`（或构建产物 `.build/cli/dist/main.js`），不得 `import '@society0/core/*'`、`@society0/host/*` 或 profile-loader 的内部文件。
- 所有成功输出都带 `schema` 版本号；新增字段只做向后兼容的追加。
- 组合检查只做静态装配（解析 lockfile、导入插件元数据、校验配置 schema、检查服务/槽位/凭据名/migration 依赖），不挂载插件、不连模型、不创建 run 目录。
- 错误统一输出到 stderr，退出码非零：`{"schema":"society0.error/1","code","message","hint"}`。

## 命令

### check

```
society0 check --profile <p> [--lock <f>] [--patches <f>] --json
```

- `--profile`：`society-profile/v2` JSON 文件。相对路径按当前工作目录解析。
- `--lock`：覆盖 profile 里的 `lockfile` 字段；不传时使用 profile 自带的相对/绝对地址。
- `--patches`：额外的 `EntryPatch[]`，与 run 时传入的覆盖层同义。
- 不写 run 目录，不启动模型。
- `ok=false` 本身仍以退出码 0 返回，因为这是一份可读的检查报告；只有无法完成检查时才返回 `society0.error/1`。

输出：

```json
{
  "schema": "society0.check/1",
  "ok": false,
  "missing": [
    {
      "kind": "slot",
      "id": "core/drivers/llm/v1",
      "requiredBy": "proposal-sequence",
      "hint": "补上提供 core/drivers/llm/v1 的 driver 插件（例如 @society0/core/driver-llm 或 @society0/core/driver-rule），或改掉 proposal-sequence 里的 driver"
    }
  ],
  "warnings": [],
  "resolved": {
    "plugins": ["@society0/core/actions", "@society0/core/driver-rule"],
    "slots": { "core/drivers/rule": ["driver-rule"] }
  }
}
```

`kind` 取值与判定方式：

| kind | 含义 | 判定 |
|---|---|---|
| `plugin` | 条目找不到模块绑定，或绑定的宿主 ABI/作用域不合法 | 解析 lockfile 中的包、侧车元数据与导入 |
| `service` | 条目 inject 的服务没有提供者、提供者作用域倒置、协议不兼容或存在重复提供者 | 与挂载前的依赖图检查同一套规则 |
| `config` | 配置 schema 不合法或出现未知字段 | 逐条目执行有效配置校验；`id` 形如 `<entryId><jsonPointer>` |
| `slot` | 配置里请求的 driver 槽位没有对应插件提供 | 扫描未禁用的 `@society0/core/driver-*` 的 `name`（缺省为 `rule` / `llm/v1`）与配置中的 `driver` 值 |
| `credential` | 模型插件引用的凭据名没有在 `@society0/core/model-credentials` 的 `stores[].reference` 注册 | 只比较名称，不读取 key 或文件 |
| `migration` | migration 的 `after` 依赖不在本次装配的 migration 集合里，或 migration 模块无法加载 | 复用 run 的同一条 migration 收集链 |

`resolved.plugins` 是未禁用条目的插件名（排序去重），`resolved.slots` 是静态可识别的 driver 槽位到提供者条目 id 的映射。

静态检查的边界：

- 只覆盖 profile 能表达的内容。数据集、外部事件里临时出现的 driver 名无法从 profile 推断，仍会在运行时报 `SLOT_PENDING`。
- `social-sqlite` / `social-codex-sqlite` 需要平台显式注册凭据名；profile 自带 `stores` 时 check 会认为完整。
- `modelCredentials`、`slots` 这类宿主启动时注入的服务由 CLI 在检查时按 profile 是否含模型插件补齐，不会算作缺失。

### run start

```
society0 run start --profile <p> --dir <run> [--lineage <d>] [--ticks N] [--lock <f>] [--patches <f>] --detach --json
```

在后台启动一个独立进程，立即返回：

```json
{"schema":"society0.run/1","runId":"run-dir-name","pid":12345,"dir":"/abs/run","state":"running"}
```

- `runId` 取 run 目录的 basename；后台日志写到同级 `<dir>.log`。
- `--dir` 必须是新目录；`--lineage` 缺省为 `<dir 的父目录>/lineages/<dir 的 basename>`，必须不存在。
- `--ticks N` 只在 profile 含 `@society0/core/proposal-sequence` 时可用：把 proposal 序列补成 N 个 tick，序列耗尽后 run 以 `completed` 结束。
- `start` 在启动前会先做一次与 `check` 相同的装配检查；检查不通过直接返回 `CHECK_FAILED`，不会留下 run 目录。
- 不带 `--detach` 时在当前进程运行到结束，再输出一次 status（便于本地调试）。

### run resume

```
society0 run resume --dir <run> --detach --json
```

从 run 目录已发布的完整点（frontier）续跑。profile、lock、patches、ticks、lineage 都从 `entry-status.json` 读取，不接受重复指定。进程仍存活时返回 `RUN_ALREADY_RUNNING`；已完成时返回 `RUN_COMPLETED`。

### run status

```
society0 run status --dir <run> --json
```

不连接正在运行的进程，只读 run 目录里的 `entry-status.json` 与 pid：

```json
{
  "schema": "society0.run-status/1",
  "runId": "run-dir-name",
  "dir": "/abs/run",
  "state": "running",
  "completedTicks": 2,
  "frontier": {"codec":"staged/1","value":"2","logicalTime":"2","completedCount":"2","pendingAcknowledgements":[]},
  "lastReason": null,
  "pid": 12345,
  "updatedAt": "2026-11-09T08:00:00.000Z"
}
```

状态机：

- `running`：状态文件记为 running 且 pid 仍存活。
- `paused`：进程主动暂停（收到 SIGTERM 在边界停下），或状态仍为 running 但 pid 已消失（`lastReason` 为 `process-exited-without-status`）。
- `completed`：引擎返回 `completed`，`lastReason` 通常是 `sequence exhausted`。
- `failed`：引擎返回 `failed` 或 worker 抛错，`lastReason` 是原始错误码/消息。

`completedTicks` 来自完整点 frontier 的 `completedCount`，运行中由 worker 每 200ms 刷新一次，可能略落后于实际进度。

### run result

```
society0 run result --dir <run> --json
```

返回 `entry-result.json`：

```json
{
  "schema": "society0.run-result/1",
  "runId": "run-dir-name",
  "dir": "/abs/run",
  "state": "completed",
  "result": {"status":"completed","cut":{"frontier":{"completedCount":"2"}}},
  "activations": null,
  "activationsNote": "D1 尚未合入；activations 摘要字段保留为空"
}
```

`result` 是 run-controller 的 `RunResult` 原样结果。D1（judge）尚未合入主线，因此预留 `activations` 字段并保持为 `null`；合入后在这里追加摘要，不改变既有字段。未完成时返回 `RUN_NOT_COMPLETED`，已完成但文件丢失时返回 `RESULT_MISSING`。

## 退出码

| 退出码 | 场景 |
|---|---|
| 0 | check 完成（含 `ok=false`）、run 命令成功、status/result 读取成功 |
| 1 | `society0.error/1`：参数以外的平台错误，例如文件不存在、run 未完成、check 未通过导致 start 拒绝 |
| 2 | 用法错误（缺参数、未知子命令） |

前台 `run start` / `run resume` 以 worker 的退出码返回：`completed` 为 0，`paused` / `failed` 为 1。

## 测试

`tests/d5-entry.test.mjs` 覆盖：

1. 缺 driver 槽位、缺服务、缺 migration、未知插件、非法配置、未注册凭据名的 `missing` 条目与 `hint`；完整 profile 返回 `ok=true` 且不创建 run 目录。
2. fake-provider 之外的 `rule-sqlite` 2 tick：detach → status 轮询到 `completed` → 读 result；以及发布完整点后 SIGTERM，status 为 `paused`/`failed`，`resume` 从 frontier 完成后 `completedTicks=200`。
3. check 输出的快照（`tests/d5-snapshots/check-rule-sqlite.json`）与 status/error 的字段集合。
