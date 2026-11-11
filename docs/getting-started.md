# 安装与第一次运行

## 准备

需要 Node.js 24.16 或更高版本、Python 3、Git 和 POSIX `patch`。规则驱动的 run 不需要模型账号，也不访问网络。

两种使用方式：

- 从发行包安装：按[公开 SDK 使用](../typescript/docs/public-usage.md)的安装一节，把发行包提供的 tarball 装进一个空目录，再把 `examples/` 和 `profiles/` 复制进去。安装完成后使用 `node_modules/.bin/society0` 或 `npx society0`。
- 从源码构建：在仓库的 `typescript/` 目录执行 `npm ci --ignore-scripts` 和 `npm run build`。构建输出在 `.build/`，CLI 入口是 `.build/cli/dist/main.js`。

下面的命令按源码构建写。当前工作目录是 `typescript/`。

```sh
npm ci --ignore-scripts
npm run build
```

`npm run build` 会重新应用补丁、编译发布包；运行测试用 `npm test`。

## 生成模块锁

profile 通过 `lockfile` 记录实际加载的包版本、源码提交和补丁顺序。第一次运行前生成一份锁：

```sh
node .build/cli/dist/main.js lock --base .build --out module-lock.json \
  @society0/host @society0/core @society0/extras @society0/social
```

输出是一份 `society0.lock/1` JSON，`packages` 中每个包带 `version`、`gitCommit`、`dirty`、`patches` 和 `hostAbi`。`dirty` 为 `false` 表示构建来源没有未提交改动。

## 检查 profile

`check` 只做静态装配，不创建 run，也不连模型：

```sh
node .build/cli/dist/main.js check --profile profiles/rule-sqlite.json \
  --lock module-lock.json --base .build --json
```

关键字段：

- `schema`：`society0.check/1`
- `ok`：`true` 表示没有缺失项
- `missing`：缺失的插件、服务、配置、槽位、凭据、migration、主体或 world 约束
- `warnings`：不阻止启动的提醒
- `resolved.plugins`：实际参与装配的插件名
- `resolved.slots`：槽位到提供者条目 id 的映射
- `migrations`：本次装配会收集的 schema 所有者

`ok` 为 `false` 时退出码是 `2`，检查本身无法完成时退出码是 `1`。

## 启动规则 run

`profiles/rule-sqlite.json` 装配两名主体 Alice 和 Bob，用一个 staged proposal 触发一次规则激活。`--ticks 1` 让 run 在完成一个 tick 后结束。

```sh
node .build/cli/dist/main.js run start --profile profiles/rule-sqlite.json \
  --dir runs/rule --ticks 1 --lock module-lock.json --base .build --detach --json
```

`--detach` 启动独立后台进程，命令立即返回：

```json
{"schema":"society0.run/1","runId":"rule","pid":12345,"dir":".../runs/rule","state":"running"}
```

不传 `--detach` 时，当前进程执行 worker，结束时打印最终状态。新建 run 要求 `--dir` 和 lineage 目录都尚未使用；重复使用同一目录会报 `FRESH_RUN_EXISTS` 或 `FRESH_LINEAGE_EXISTS`。

## 查看状态与结果

```sh
node .build/cli/dist/main.js run status --dir runs/rule --json
```

`run status` 的字段：

- `state`：`running`、`paused`、`completed` 或 `failed`
- `completedTicks`：已经发布的完整 tick 数
- `frontier`：当前完整点对应的 frontier，带 `codec`、`value`、`logicalTime`、`completedCount`
- `lastReason`：结束或暂停原因，例如 `max ticks reached (1)`
- `pid`、`updatedAt`：最后写入状态的进程和时间

run 结束后读取结果：

```sh
node .build/cli/dist/main.js run result --dir runs/rule --json
```

结果包含 `schema: "society0.run-result/1"`、`state`、`result` 和 `activations`。`result.cut` 是最后一个完整点，`result.cut.frontier.completedCount` 为已完成的推进数；`activations` 汇总激活总数、完成数、失败数、失败原因和预算上限。规则 profile 的示例输出中 `activations.total` 和 `activations.completed` 都是 `1`。

## 续跑

只有 `paused` 或 `failed` 的 run 可以续跑。`completed` 的 run 没有可继续的 frontier，`resume` 会返回 `RUN_COMPLETED`；还没有结果的 run 用 `run result` 会返回 `RUN_NOT_COMPLETED`。

`profiles/event-sqlite.json` 会等待事件输入，适合观察暂停与续跑。启动后，状态保持 `running`：

```sh
node .build/cli/dist/main.js run start --profile profiles/event-sqlite.json \
  --dir runs/event --lock module-lock.json --base .build --detach --json
```

向 `run start` 返回的 `pid` 发送 `SIGTERM` 后，`run status` 会显示：

```json
{"schema":"society0.run-status/1","runId":"event","state":"paused","completedTicks":0,"frontier":null,"lastReason":"process-exited-without-status"}
```

然后从最后一个完整点继续：

```sh
node .build/cli/dist/main.js run resume --dir runs/event \
  --lock module-lock.json --base .build --detach --json
```

`run resume` 返回新的 `society0.run/1` 和新的 `pid`。它沿用 run 目录里记录的原 profile、lock、lineage 和 ticks；已经完成的 run 不会被重新初始化。

## run 目录

一次 run 的主要文件都在 `--dir` 指向的目录里：

- `world.sqlite`：主体、领域数据和当前投影
- `history/`：完整点、历史段和 roots
- `artifacts/`：大于内联预算的外部对象
- `bootstrap.json`：创建时的入口和 schema 信息
- `entry-status.json`、`entry-result.json`：平台读取的状态和结果
- lineage 目录（默认在 run 目录同级的 `lineages/<runId>`）：Attempts 证据和来源身份

`--lineage` 可以指定外部 authority。恢复和分叉时保留原 lineage 目录与 identity；删除 world 目录不会自动删除 lineage 证据。

## 下一步

- 改 profile、加插件：[profile 与插件](profiles-and-plugins.md)
- 接数据和初始资料：[数据与数据集](datasets.md)
- 设计对照实验：[因果分支](causal-branches.md)
