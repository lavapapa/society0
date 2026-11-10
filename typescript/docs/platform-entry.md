# 平台入口（D7）

平台（Society0 工作台）不 import Next 的内部模块，只通过 `society0` CLI 的带版本 JSON 交互。CLI 每次调用都是独立进程；状态和结果都落在 run 目录里，因此平台重启或 CLI 冷启动不影响已有 run。

约定：

- 平台、脚本和其他语言只能调用 `node_modules/.bin/society0`（或构建产物 `.build/cli/dist/main.js`），不得 `import '@society0/core/*'`、`@society0/host/*` 或 profile-loader 的内部文件。
- 所有成功输出都带 `schema` 版本号；新增字段只做向后兼容的追加。
- 组合检查只做静态装配（解析 lockfile、导入插件元数据、校验配置 schema、检查服务/槽位/凭据名/migration 依赖），不挂载插件、不连模型、不创建 run 目录。
- 错误统一输出到 stderr，退出码非零：`{"schema":"society0.error/1","code","message","hint"}`。

## 命令

### check

```
society0 check --profile <p> [--lock <f>] [--patches <f>] [--world <f>] [--base <d>] [--credentials-env <NAME>] --json
```

- `--profile`：`society-profile/v2` JSON 文件。相对路径按当前工作目录解析。
- `--lock`：覆盖 profile 里的 `lockfile` 字段；不传时使用 profile 自带的相对/绝对地址。
- `--patches`：额外的 `EntryPatch[]`，与 run 时传入的覆盖层同义。
- `--world`：可选的 world 文件，与 `run start` 共用同一个读取与校验函数：必须是 JSON 对象，`actors` 段每条都要有 `id` 和 `source`。给了 world 时，profile 初始化插件引用到的主体 id 必须在 `actors` 段里；没给 world 但 profile 引用了主体时，`warnings` 里提示 run 必须提供 world，不影响 `ok`。给了 world 且前面的静态检查没有缺失时，还会在 `TMPDIR` 下建临时目录，执行一次与 `run start` 完全相同的根创建：迁移、`actors.seed`、各个 initializer（`social.seed` 等），跑完直接丢弃。`UNIQUE`、`FOREIGN KEY`、`CHECK`、`ACTOR_MISSING`、`ACTOR_SOURCE_MISSING` 等根创建错误会转成 `kind: "world"` 的 `missing` 项并附修复提示；不会写入目标 run 目录。
- `--base`：插件解析目录；module-resolver 从这个目录的 `node_modules` 按包名查找插件（见“从实验目录加载本地插件”）。
- `--credentials-env`：环境变量名。只检查这个变量存在且是 JSON 对象，并把 profile 里出现的凭据名视为已注册；不读取也不打印 key。
- 不写 run 目录，不启动模型。
- `ok=true` 退出 0；`ok=false` 退出 2；检查本身无法完成（缺 profile、JSON 损坏、装配抛错）时输出 `society0.error/1` 并退出 1。

输出：

```json
{
  "schema": "society0.check/1",
  "ok": false,
  "missing": [
    {
      "kind": "slot",
      "code": "SLOT_PROVIDER_MISSING",
      "id": "core/agent-runtimes/pi/v1",
      "requiredBy": "driver-llm",
      "hint": "槽位 core/agent-runtimes/pi/v1 没有任何活动条目提供；已安装的 @society0/core/agent-runtime-pi 声明提供该槽位，把对应条目加入 profile（driver-llm 需要它）"
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
| `slot` | 某个激活条目的 `requires` 槽位，或配置里请求的 driver 槽位，没有活动条目提供 | 对活动条目展开 `api.slots`（用合并后的 config 替换占位符），再扫描配置里所有 `driver` 值；缺失时报 `SLOT_PROVIDER_MISSING` |
| `credential` | 模型插件引用的凭据名没有在 `@society0/core/model-credentials` 的 `stores[].reference` 注册 | 只比较名称，不读取 key 或文件 |
| `migration` | migration 的 `after` 依赖不在本次装配的 migration 集合里，或 migration 模块无法加载 | 复用 run 的同一条 migration 收集链 |
| `actor` | 同时有多个插件会写 `s0_actors`，或领域 initializer 没有声明 `after: ["actors.seed"]`、或声明了但 profile 没开 `actors` 的 `seed` | 读 initializer 的静态依赖；只覆盖已登记的 Core/social 插件 |
| `world` | 临时数据库执行 run start 的根创建时违反表约束，或 world 里的主体缺 `source` / 根创建引用的主体不存在 | 带 `--world` 时复用 run 的根创建代码实际跑一遍；错误信息翻成表名、列名和对应的 profile 条目 |

`resolved.plugins` 是未禁用条目的插件名（排序去重），`resolved.slots` 是静态可识别的槽位到提供者条目 id 的映射。`check` 和 `run start` 前的装配检查使用同一个 `slotGraph(plan)`。

### actionFiles 标记服务

`@society0/core/action-files` 提供 `actionFiles` 服务（`protocol` 同名，major 1，无 capabilities）。服务值只有 `{ offersPath, catalogPath }`，分别是 `/self/actions/offers.jsonl` 与 `/self/actions/catalog.jsonl` 两个完整行动清单文件视图的路径。需要在自身产物里引用这两个路径的插件（例如经济简报的文件清单）应在 `api.requires` 声明 `actionFiles`，让 profile compose 按服务键把 action-files 带上；不引用这两个路径的插件不需要声明。

### 槽位依赖声明（`slots`）

插件入口可以导出可选的 `api.slots`，声明自己提供和需要哪些运行期槽位：

```json
{
  "exportPath": "./driver-llm",
  "provides": [{"key": "driver.llm", "protocol": "driver.llm", "major": 1, "capabilities": []}],
  "requires": [],
  "slots": {
    "provides": ["core/drivers/{name}"],
    "requires": ["core/agent-runtimes/{runtime}"]
  }
}
```

`provides` 和 `requires` 都是字符串数组。字符串里的 `{配置键}` 会用该条目合并默认值后的有效 `config` 替换；点号表示嵌套键。例如 `driver-llm` 的 `name` 默认 `llm/v1`、`runtime` 默认 `pi/v1`，展开后是 `core/drivers/llm/v1` 和 `core/agent-runtimes/pi/v1`。`driver-rule` 的 `name` 默认 `rule`；`agent-runtime-pi` 静态提供 `core/agent-runtimes/pi/v1`；`measurements` 静态提供 `core/drivers/measurements`。第三方插件用同样的字段声明即可，不要求包名或模块路径符合 `@society0/core/driver-*`。

`slots` 是静态 JSON，会原样进入侧车的 `plugin.api.json`，因此 `check` 和 `s0 compose` 不需要导入插件就能读取。

运行期槽位使用和静态声明要保持这三条对应关系，`tests/d19-slot-audit.test.mjs` 会在装配全部 Core/extras/social 插件的 profile 上实际记录 `declare`、`contribute`、`acquire` 后检查：

- 插件向别的插件声明的 required 槽位贡献时，该 key 必须出现在本插件的 `api.slots.provides`。
- 插件声明了 required 槽位、自己又不贡献时，该 key 必须出现在本插件的 `api.slots.requires`。
- 插件获取别的插件声明的 required 槽位、自己又不贡献时，该 key 必须出现在本插件的 `api.slots.requires`。

同一个插件自己声明、自己贡献、自己获取的槽位属于自给自足，不需要写 `requires`；`provides` 仍按第一条写。例如 `model-providers`、`model-credentials` 和 `drivers` 在注册时同时声明并贡献单个 key，因此不写 `requires`。占位符先按条目的有效配置展开再比较；声明了但本次 profile 没观察到的 key 只报 warning，不判失败。第三方插件库不依赖本仓库的测试数据，应在自己的仓库里用同一套规则和 `SOCIETY0_SLOT_AUDIT` 记录方式审计。

`requires` 的槽位如果没有任何活动条目 `provides`，`check` 报 `kind:"slot"`、`code:"SLOT_PROVIDER_MISSING"`，并带上槽位 id 和 `requiredBy`；如果 lockfile 里已安装的包在其侧车里静态声明了该槽位，提示会写出包名和入口（例如 `@society0/core/agent-runtime-pi`）。带占位符的第三方声明无法在不读取配置 schema 默认值时推断，提示只给槽位 id。

静态检查的边界：

- 只覆盖 profile 能表达的内容。数据集、外部事件里临时出现的 driver 名无法从 profile 推断，仍会在运行时报 `SLOT_PENDING`。
- `social-sqlite` / `social-codex-sqlite` 需要平台显式注册凭据名；profile 自带 `stores` 时 check 会认为完整。
- `check --world` 读取 world 文件并校验 JSON 对象、actors 段的 `id` 和 `source`；静态检查通过时还会实际执行根创建，因此能发现 `UNIQUE social_pair_members.actor` 这类只在写库时才出现的约束错误。不给 `--world` 时只根据 profile 静态引用给出提示，无法发现 world 中临时出现的其他主体。
- `check --world` 的试跑只覆盖根创建（迁移、seed、initializer），不推进 tick、不启动模型；运行期才触发的约束仍要到 `run` 才能发现。临时目录在检查结束后删除。
- `modelCredentials`、`slots` 这类宿主启动时注入的服务由 CLI 在检查时按 profile 是否含模型插件补齐，不会算作缺失。

### run start

```
society0 run start --profile <p> --dir <run> [--lineage <d>] [--ticks N] [--world <f>] [--base <d>] [--credentials-env <NAME>] [--lock <f>] [--patches <f>] --detach --json
```

在后台启动一个独立进程，立即返回：

```json
{"schema":"society0.run/1","runId":"run-dir-name","pid":12345,"dir":"/abs/run","state":"running"}
```

- `runId` 取 run 目录的 basename；后台日志写到同级 `<dir>.log`。
- `--dir` 必须是新目录；`--lineage` 缺省为 `<dir 的父目录>/lineages/<dir 的 basename>`，必须不存在。
- `--ticks N` 对所有 profile 生效：把 N 写进 run-controller 的 `maxTicks`，是**上限**，不保证一定跑满。推进满 N 个 tick 后，在已发布的完整点停下，状态为 `completed`，`lastReason` 形如 `max ticks reached (N)`。来源先耗尽时（例如 round-robin 的 `rounds` 不够 N 个 tick）run 会提前 `completed`，`lastReason` 记来源给出的原因，常见为 `round-robin exhausted`。不传时由 profile 自己的终止条件决定运行长度。
- `--world <f>`：JSON 对象，作为本次 run 的 world 输入。`actors` 段形如 `[{"id","attributes","source"}]`，由 Core 的 `actors.seed` 在根创建事务里写入 `s0_actors`；`source` 必填，缺失时报 `ACTOR_SOURCE_MISSING`。路径记录在状态文件里。
- `--base <d>`：同 check，指定插件查找目录。
- `--credentials-env <NAME>`：从环境变量 `NAME` 读取凭据 JSON（结构同 model-credentials 文件存储：`{"<providerId>": {...}}`），只在当前进程内存里注册，不写 run 目录、不打印；状态文件只记录变量名。detach 时子进程继承父进程环境，命令行里只有变量名。
- `start` 在启动前会先做一次与 `check` 相同的装配检查；检查不通过直接返回 `CHECK_FAILED`，不会留下 run 目录。`--credentials-env` 在启动前会先校验 JSON，缺失或格式错误不会创建 run 目录。
- 不带 `--detach` 时在当前进程运行到结束，再输出一次 status（便于本地调试）。

### run resume

```
society0 run resume --dir <run> [--credentials-env <NAME>] --detach --json
```

从 run 目录已发布的完整点（frontier）续跑。profile、lock、patches、ticks、lineage、base、world、credentials-env 都从 `entry-status.json` 读取；`--credentials-env` 可以显式覆盖，但变量值仍只在子进程内存里。进程仍存活时返回 `RUN_ALREADY_RUNNING`；已完成时返回 `RUN_COMPLETED`。

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
- `completed`：引擎返回 `completed`，`lastReason` 通常是 `sequence exhausted`、`round-robin exhausted` 或 `max ticks reached (N)`。终态写盘后，worker 的定时状态回调不会再把它改回 `running`。
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
  "activations": {
    "total": 2,
    "completed": 1,
    "failed": 1,
    "byReason": {"success": 1, "setup_error": 1},
    "limits": {},
    "failures": [
      {
        "actorId": "alice",
        "momentId": "m1",
        "reason": "setup_error",
        "code": "SLOT_PENDING",
        "stage": "runtime.acquire"
      }
    ]
  }
}
```

`result` 是 run-controller 的 `RunResult` 原样结果。`activations` 是从 `RunResult.activations.entries` 汇总出的平台摘要：

- `total` 是本次 run 记录的激活条数；`completed` 是 `requiredSatisfied=true` 的条数；`failed` 是其余条数。
- `byReason` 按每条激活的 `reason` 计数，包含成功和失败。
- `limits` 按本次 run 内预算先耗尽的额度计数，键为 `requests`、`toolCalls` 或 `turns`；没有预算耗尽时为空对象。`RunResult.activations.entries` 的每条激活摘要带同名字段 `limit`。
- `failures` 只列未完成的激活，最多 20 条；每条带 `actorId`、可选的 `momentId`、`reason`、可选的 `code`、`stage`、`slot` 和 `limit`。
- `code` 取自失败错误消息里的首个大写错误码（例如 `SLOT_PENDING`）；`stage` 取 driver 记录的执行阶段（例如 `runtime.acquire`）。

预算耗尽（`budget_exhausted`）时按已结算处理：`requiredActions` 为空，或非空且已满足，激活走 `success` 完成判定，保留已完成的动作与回执，并在 `warnings` 里记录 `BUDGET_EXHAUSTED`（无必需动作）或 `BUDGET_AFTER_REQUIRED`（必需动作已完成），`limit` 指明先用完的额度。`requiredActions` 非空且未满足时仍为 `incomplete`，run 不会推进到新的完整点。这样 tick 不再因主体只耗尽请求、工具调用或轮次预算而停在 `paused`/`recoveryRequired`，后续 resume 也不会因此报 `HISTORY_DIRTY`。

## 领域拒绝与执行故障

行动插件要区分“规则拒绝”和“执行故障”。规则拒绝表示环境按业务规则明确不允许这次行动，世界状态没有因此改变；执行故障表示插件异常、持久化失败、传输未知或平台内部错误。

规则拒绝用 core 的 `actionRejection` 声明：

```js
import { actionRejection } from '@society0/core/actions';

// 在 ActionDefinition.execute 里
throw actionRejection('ECON_BUDGET_INVALID', '资金不足，无法支付本次行动', {
  need: 10,
  have: 7,
});
```

契约：

- `actionRejection(code, message, details?)` 返回带 `rejection: true` 的 `Error`；`isActionRejection(value)` 只认这个显式标记，不按错误码猜测。
- 执行器捕获该错误后返回 `status: 'rejected'` 的回执，`value` 形如 `{ code, message, details?, action, target? }`，与现有 rejected 回执兼容；这次行动不记为 attempt 失败。
- `execute` 自己返回 `status: 'rejected'` 的回执也按同一规则处理。工具结果文本保留 `code` 和 `message`，主体能看到拒绝原因。
- 激活统计把这类回执计入 `rejected`（内部计数 `actRejected`），不计入 `actFailed`，也不设置 `toolFailed`；只要 `requiredActions` 已满足，激活按 `success` 结算，并在 `warnings` 里记录 `ACTION_REJECTED`。回执原样保留在 activation evidence 里。
- `RunResult.activations.entries[].rejected` 是该次激活按领域拒绝结算的 act 数量。

仍判 `incomplete` 的情况：

- 未带 `rejection:true` 的抛错（插件执行异常、schema 输出不符等）；输出不符仍报 `ACTION_OUTPUT`。
- 持久化、传输、平台错误，以及基础设施错误码（`TOOL_*`、`INFERENCE_*`、`ATTEMPT_*`、`SQLITE_*` 等现有集合）。即使 `actionRejection` 带了这些码，也不按领域拒绝结算。
- 平台不可用类回执（`ACTION_UNAVAILABLE`）不走领域拒绝计数，仍按原有未知回执处理。
- `requiredActions` 非空且未满足：按 D20 的规则返回 `required_action_failed`，run 不会推进到新的完整点。

未完成时返回 `RUN_NOT_COMPLETED`，已完成但文件丢失时返回 `RESULT_MISSING`。

## lock

```
society0 lock --base <d> --out <lock.json> <package>... --json
```

从 `--base` 的 `node_modules` 按包名找到每个包，读取 `package.json` 的 `version`，从包所在 git 仓库读取 `gitCommit` 与 `dirty`，`patches` 取包侧车 `source.patches`，`hostAbi` 固定为 `society0/host/1`，写出 `{lockfile, packages}`。输出：

```json
{"schema":"society0.lock/1","lockfile":"file:///abs/lock.json","packages":{"@fixture/plugin":{"version":"1.2.3","gitCommit":"…","dirty":true,"patches":[],"hostAbi":"society0/host/1"}}}
```

包不在 git 仓库内、没有提交，或名字在 `--base` 下找不到时报 `society0.error/1`（`LOCK_GIT` / `PACKAGE_MISSING`）。写出的 lock 必须覆盖 profile 引用的全部包；module-resolver 只接受包名，不接受路径，并要求 lock 条目带 `version`、`gitCommit`、`dirty`、`patches`、`hostAbi`。

## 从实验目录加载本地插件

实验目录的结构：

```
<dir>/
  node_modules/@society0/{host,core,extras,social} -> 已构建包
  node_modules/@fixture/plugin -> ../plugin      # 软链到本地插件包
  plugin/package.json  index.js  plugin.meta.json  plugin.schema.json  plugin.api.json
  lock.json
  profile.json
```

本地插件包至少要有 `package.json`（`name`、`version`、`type: "module"`、`exports`、`society.plugin` 三个侧车路径、`peerDependencies` 里包含 `@society0/host`）、入口 JS 和三份侧车。侧车里的 `source.gitCommit`/`dirty`/`patches` 必须和 `lock` 得到的一致：最省事的做法是把代码提交一次，侧车文件保持未跟踪（于是 `dirty=true`，`gitCommit` 仍是那次提交）。推荐步骤：

1. 在实验目录放好 `node_modules` 软链与本地插件包；
2. `society0 lock --base <dir> --out <dir>/lock.json <本地包> @society0/host @society0/core @society0/extras @society0/social`；
3. profile 的 `lockfile` 指向 `<dir>/lock.json`；
4. `society0 check --profile <dir>/profile.json --base <dir> --json`；
5. `society0 run start --profile <dir>/profile.json --dir <run> --base <dir> --ticks 1 --detach --json`。

`--base` 只改变插件解析的起点，不会修改 `node_modules` 或安装包；实验目录之外仍用 profile 自带的 lockfile。

本地插件可以自带 migration。入口模块导出 `migrations`，值是数组，或 `(source) => 数组`；每个元素的形状与 `@society0/core/sqlite/contracts` 的 `SqliteMigration` 相同：

```js
export const migrations = [{
  owner: 'my-domain',
  from: 0,
  to: 1,
  after: [],                       // 依赖的其他 migration owner
  source: { package: 'my-plugin', entry: '.', version: 1 },
  statements: [{ sql: 'CREATE TABLE my_domain_rows (id TEXT PRIMARY KEY NOT NULL)' }]
}];
```

platform 按同一约定收集所有活动入口的 `migrations` 导出，包括内置的 core/social 插件；不再维护额外的内置 migration 名单。复制安装到实验 `node_modules` 的包和以 symlink 安装的包都按 run 加载插件时的同一解析结果读取。没有 `migrations` 导出的插件会被跳过。同一个 `owner+from+to` 只收一次；内容不同的同名 migration 会报 `kind: 'migration'` 冲突。`check` 的 JSON 输出里 `migrations` 列出实际收到的 owner。

## world 与 actors

`--world` 的 `actors` 段是 Core `actors.seed` 的唯一输入，每个记录为 `{id, attributes, source}`。本单采用做法 A（D6 §2）：`s0_actors` 只由 `actors.seed` 在根创建事务里写一次；`social-init` 的 `social.seed` 声明 `after: ["actors.seed"]`，`social-repository.addMember` 只写 `social_members`，actor 不存在时报 `ACTOR_MISSING`。profile 里 `@society0/core/actors` 需要显式开启：

```json
{"id": "actors", "name": "@society0/core/actors", "config": {"seed": true}}
```

旧的 `plain-init` 仍可用 `initialActors` 自建 actor，但同一个 profile 里不要同时开启 `actors.seed` 与 `plain-init`，否则 `check` 会报 `kind: "actor"`。经济侧（私有包）的迁移不在本单范围内。

### 必需 initializer 的推导

根创建时传给 `sqlite-bootstrap` 的 `requiredInitializerIds` 从装配后的 plan 推导，不再按 profile 写死某一种 seed：

- `@society0/core/actors` 的 `config.seed` 为 `true` 时，要求 `actors.seed`；
- 启用 `@society0/core/plain-init` 时，要求 `plain.seed`；
- 启用 `@society0/social/social` 时，再要求 `social.seed`。

`run start` 和 `check --world` 的试跑走同一个根创建入口，因此用的是同一份推导结果。列表按上面的顺序生成；某个插件登记的 initializer 只有在实际加载后才会进入 initializer 集合，缺少必需项时 `sqlite-bootstrap` 仍会报 `INITIALIZER_MISSING`。profile 没有任何 actor 创建者时，推导结果为空，`check` 保持原来的行为，只提示 actor 相关缺失，不额外报 `INITIALIZER_MISSING`。

## 冻结上下文与默认感知

`/context` 下的冻结文件按主体和 Moment 命名，不再使用 `operationId` 的百分号编码：

```
/context/<actorId>-<momentId>-<index>-<sequence>.txt
```

- `actorId`、`momentId` 只保留 ASCII 字母、数字和 `- _ .`；其他字符替换为 `_`。发生过替换、截断或原值为空时，该段追加 `.` 加原值 SHA-256 的前 8 位十六进制，避免不同原名落到同一路径。
- 每段最多 48 个安全字符；`index` 是同一冻结内的文件序号。
- `sequence` 是同一 `actorId + momentId` 下的冻结序号，从 0 开始递增。一次激活会同时持有多份冻结（提示上下文和每个感知页），激活重试也会再次冻结，因此序号必须保留；序号由调用顺序确定，不使用随机数。
- Driver 提示里给出的“冻结上下文”路径就是 `freeze` 的真实返回值，模型可以按原样交给 `read` 工具。

感知源在 `PerceptionSource` 上可以声明可选的 `defaultDrivers`，值是 Driver 名数组，`'*'` 表示所有 LLM Driver。`perceptions` 服务提供 `defaultsFor(driverName)`，按注册顺序返回命中的源 id。`driver-llm` 的 `defaultPerceptions` 默认为 `true`：实际获取的列表是配置里的 `perceptions` 在前，再把 `defaultsFor(name)` 去重追加在后。设为 `false` 时只使用显式 `perceptions`，与未引入默认挂载前的行为一致；没有任何源声明 `defaultDrivers` 时，默认值为 `true` 也不会改变现有 profile 的感知列表。

## 退出码

| 退出码 | 场景 |
|---|---|
| 0 | check 返回 `ok=true`、run 命令成功、status/result 读取成功 |
| 1 | `society0.error/1`：参数以外的平台错误，例如文件不存在、run 未完成、check 未通过导致 start 拒绝、凭据环境变量缺失或格式错误 |
| 2 | check 返回 `ok=false`；用法错误（缺参数、未知子命令） |

前台 `run start` / `run resume` 以 worker 的退出码返回：`completed` 为 0，`paused` / `failed` 为 1。

## 测试

`tests/d5-entry.test.mjs` 与 `tests/d7-entry2-*.test.mjs` 覆盖：

1. 缺 driver 槽位、缺服务、缺 migration、未知插件、非法配置、未注册凭据名的 `missing` 条目与 `hint`；完整 profile 返回 `ok=true` 且不创建 run 目录。
2. fake-provider 之外的 `rule-sqlite` 2 tick：detach → status 轮询到 `completed` → 读 result；以及发布完整点后 SIGTERM，status 为 `paused`/`failed`，`resume` 从 frontier 完成后 `completedTicks=200`。
3. check 输出的快照（`tests/d5-snapshots/check-rule-sqlite.json`）与 status/error 的字段集合。
4. social replay profile 的 `--ticks 2` 在完整点结束（D7-1）；check 的 0/2/1 退出码与 actor 规则（D7-2、D7-5）。
5. `--credentials-env` 不把 key 写进 run 目录或日志（D7-3）；实验目录本地插件的 lock/check/run（D7-4）。
6. `actors.seed` 写入 `s0_actors`、缺 `source` 报 `ACTOR_SOURCE_MISSING`、`addMember` 缺 actor 报 `ACTOR_MISSING`（D7-5）。
