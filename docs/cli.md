# 命令行

`society0` 的每次调用都是独立进程。状态和结果写在 run 目录里，所以平台重启或 CLI 冷启动都不会丢失已有 run。

所有平台命令都输出一行 JSON。成功输出的 `schema` 带版本；错误写到标准错误，退出码非零：

```json
{"schema":"society0.error/1","code":"RUN_NOT_COMPLETED","message":"...","hint":"..."}
```

`--json` 可以显式写出，但输出始终是 JSON。

## 帮助

```sh
node .build/cli/dist/main.js --help
```

帮助列出五组命令：`check`、`lock`、`run start`、`run resume`、`run status`、`run result`，以及兼容的 `--boot` 入口。

## check

```sh
node .build/cli/dist/main.js check --profile <p> [--lock <f>] [--patches <f>] \
  [--world <f>] [--base <d>] [--credentials-env <NAME>] --json
```

`check` 只做静态装配：解析 profile 和 lockfile、导入插件元数据、校验配置、检查服务、槽位、凭据名、migration 和主体引用。它不挂载插件，不连模型，也不创建 run 目录。

- `--profile`：`society-profile/v2` 文件；相对路径按当前工作目录解析。
- `--lock`：覆盖 profile 里的 `lockfile`。
- `--patches`：额外的 `EntryPatch[]`，与 run 时传入的覆盖层同义。
- `--world`：可选的 world 文件。给了 world 且没有静态缺失时，`check` 会在 `TMPDIR` 下建一个临时目录，执行一次与 `run start` 相同的根创建，然后丢弃。
- `--base`：插件解析目录；module-resolver 从这个目录的 `node_modules` 按包名找插件。
- `--credentials-env`：环境变量名。只检查变量存在且是 JSON 对象，并把 profile 中出现的凭据名视为已注册；不读取也不打印 key。

成功输出：

```json
{"schema":"society0.check/1","ok":true,"missing":[],"warnings":[],
 "resolved":{"plugins":["@society0/core/actions","..."],"slots":{"core/drivers/rule":["driver-rule"]}},
 "migrations":["actions","actors","artifacts-local","moment-views","perception","public","results","thread"]}
```

`missing` 每项带 `kind`、`id`、`requiredBy` 和 `hint`。`kind` 取值：

| kind | 含义 |
| --- | --- |
| `plugin` | 条目找不到模块绑定，或宿主 ABI/作用域不合法 |
| `service` | inject 的服务没有提供者、提供者作用域倒置、协议不兼容或重复提供者 |
| `config` | 配置 schema 不合法，或出现未知字段 |
| `slot` | 激活条目需要的槽位没有活动提供者 |
| `credential` | 模型插件引用的凭据名没有注册 |
| `migration` | migration 依赖不在本次装配集合里，或模块无法加载 |
| `actor` | 多个插件写主体表，或领域 initializer 的主体依赖不成立 |
| `world` | 临时数据库执行根创建时违反表约束，或 world 缺少主体/`source` |

`ok=true` 退出 `0`，`ok=false` 退出 `2`，检查本身无法完成退出 `1`。

## lock

```sh
node .build/cli/dist/main.js lock --base <d> --out <lock.json> <package>... --json
```

从已安装包的公开元数据生成模块锁。输出 `schema: "society0.lock/1"`，`packages` 中每个包记录 `version`、`gitCommit`、`dirty`、`patches` 和 `hostAbi`。`--base` 指向包含 `node_modules` 的目录。

示例：

```sh
node .build/cli/dist/main.js lock --base .build --out module-lock.json \
  @society0/host @society0/core @society0/extras @society0/social
```

`dirty` 为 `true` 的包会在锁里保留该状态；发行验收要求 `false`。

## run start

```sh
node .build/cli/dist/main.js run start --profile <p> --dir <run> [--lineage <d>] \
  [--ticks N] [--world <f>] [--base <d>] [--credentials-env <NAME>] \
  [--detach] --json
```

创建并启动一次新 run。`--profile` 和 `--dir` 必填；`--dir` 是 run 目录，run id 取目录名。`--lineage` 指定 Attempts authority，默认在 run 目录同级建立 `lineages/<runId>`。`--ticks` 覆盖 `run-controller` 的 `maxTicks`，必须是正整数；达到上限时 run 以 `max ticks reached (N)` 结束。

`--detach` 启动后台 worker 并立即返回：

```json
{"schema":"society0.run/1","runId":"rule","pid":12345,"dir":".../runs/rule","state":"running"}
```

不传 `--detach` 时，当前进程执行 worker，结束时打印最终 `society0.run-status/1`。例如：

```json
{"schema":"society0.run-status/1","runId":"rule-fg","state":"completed","completedTicks":1,
 "frontier":{"codec":"staged/1","value":"1","logicalTime":"1","completedCount":"1","pendingAcknowledgements":[]},
 "lastReason":"max ticks reached (1)"}
```

新建要求 world 目录和 lineage 目录尚未使用；重复使用会报 `FRESH_RUN_EXISTS` 或 `FRESH_LINEAGE_EXISTS`。检查失败会报 `CHECK_FAILED`，并提示先运行 `society0 check`。

## run status

```sh
node .build/cli/dist/main.js run status --dir <run> --json
```

读取 `entry-status.json`。输出字段：

- `runId`、`dir`
- `state`：`running`、`paused`、`completed`、`failed`
- `completedTicks`
- `frontier`：完整点的 `codec`、`value`、`logicalTime`、`completedCount`、`pendingAcknowledgements`
- `lastReason`
- `pid`（存在时）
- `updatedAt`

如果状态写的是 `running` 但记录的 pid 已经不存在，命令会把 `state` 读成 `paused`，并把 `lastReason` 补成 `process-exited-without-status`。这让进程被外部终止后仍然可以判断下一步。

## run resume

```sh
node .build/cli/dist/main.js run resume --dir <run> [--credentials-env <NAME>] --detach --json
```

从已有 run 的最后一个完整点继续。它沿用状态文件里记录的原 profile、lock、patches、lineage、ticks、world 和 `--base`；没有显式传入时不会改用新的输入。恢复跳过初始化 seed，并核验 lineage authority。

只接受 `paused` 或 `failed` 的 run：

- `running` 且 pid 仍存活：`RUN_ALREADY_RUNNING`
- `completed`：`RUN_COMPLETED`，提示用 `run result`
- 缺少状态文件：`RUN_NOT_FOUND`

成功时输出新的 `society0.run/1` 和新的 `pid`。

## run result

```sh
node .build/cli/dist/main.js run result --dir <run> --json
```

读取 `entry-result.json`。输出：

```json
{"schema":"society0.run-result/1","runId":"rule","state":"completed",
 "result":{"status":"completed","cut":{"id":"...","frontier":{"completedCount":"1", "...": "..."}}},
 "activations":{"total":1,"completed":1,"failed":0,"byReason":{"completed":1},"limits":{},"failures":[]}}
```

`result.cut` 是最后一个完整点，包含 `id`、`sequence`、`revision`、`generation`、`controlSequence`、`schema`、`frontier`、`segments`、`artifacts`、`requiredOperations` 和 `evidence`。`activations` 的 `limits` 记录预算先用完的额度，`failures` 最多列出 20 条失败激活。

run 还在运行或暂停时没有结果，返回 `RUN_NOT_COMPLETED`；状态为 completed 但结果文件缺失时返回 `RESULT_MISSING`。

## 通用地址与覆盖

- `--patches`：`EntryPatch[]` JSON 文件。`config` 会替换该入口的整个配置对象，覆盖时保留必填字段。
- `--base`：插件解析目录。源码构建时指向 `typescript/.build`；发行安装时指向包含 `node_modules` 的消费者目录。
- `--lineage`：外部 authority 目录。恢复和分叉时要与源 run 使用同一个 lineage 目录和 identity。
- `--credentials-env`：凭据环境变量。值只进入进程内存，不写入磁盘或日志。

分支不在 `society0` CLI 里；它由 `examples/run-profile-branch.mjs` 或公开 SDK 的 `forkProfile` 调用 RunBranch 服务完成，见[因果分支](causal-branches.md)。
