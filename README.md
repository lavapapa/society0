# Society0

Society0 是一个多主体社会仿真平台。主体共享同一个世界，插件负责信息、行动和机制；规则驱动和模型驱动使用同一套主体接口。运行状态、完整 Thread 与结果保存在本地 SQLite 中，可以从完整点恢复，也可以从同一起点分叉出平行世界。

平台由几层组成：`@society0/core` 提供运行内核，`@society0/extras` 提供数据集、记忆、向量和 workspace，`@society0/social` 提供关系、内容、传播、推荐与测量；`@society0/host` 负责插件装配和生命周期，`@society0/cli` 提供 `society0` 命令。所有内容都落在 run 目录里，运行、读取结果和恢复不需要图形界面。

## 能做什么

- 用规则或模型驱动一组主体，在显式时间安排下推进仿真。
- 用插件组合世界机制，而不改动内核：信息源、行动、传播、推荐、测量各自独立装配。
- 保存每个完整点的状态、Thread 和外部证据，之后继续运行或读取历史。
- 从同一个完整点复制出多个目标 run，只改一个干预或参数，比较不同世界的结果。
- 用数据集、主体初始资料和微观群体设定把研究问题变成可重复的实验。

## 快速上手

需要 Node.js 24.16 或更高版本、Python 3、Git 和 POSIX `patch`。下面的命令在源码树里构建，并跑通一条规则驱动的 run：

```sh
git clone https://github.com/lavapapa/society0.git
cd society0/typescript
npm ci --ignore-scripts
npm run build

node .build/cli/dist/main.js lock --base .build --out module-lock.json \
  @society0/host @society0/core @society0/extras @society0/social
node .build/cli/dist/main.js check --profile profiles/rule-sqlite.json \
  --lock module-lock.json --base .build --json

node .build/cli/dist/main.js run start --profile profiles/rule-sqlite.json \
  --dir runs/rule --ticks 1 --lock module-lock.json --base .build --detach --json
node .build/cli/dist/main.js run status --dir runs/rule --json
node .build/cli/dist/main.js run result --dir runs/rule --json
```

`check` 成功时输出 `"ok": true`，并列出实际装配的插件、槽位提供者和 migration。`run start` 返回后台进程的 `pid`；`run status` 显示 `state`、`completedTicks` 和 `frontier`；`run result` 返回 `activations` 与最后一个完整点。规则 profile 不需要模型账号。

`run resume` 用于 `paused` 或 `failed` 的 run，它从最后一个完整点继续。要看到 `paused`，可以用 `profiles/event-sqlite.json` 启动一个等待事件的 run，在 `running` 时向进程发送 `SIGTERM`，然后执行：

```sh
node .build/cli/dist/main.js run resume --dir runs/event \
  --lock module-lock.json --base .build --detach --json
```

已经 `completed` 的 run 没有可续跑的 frontier，`resume` 会明确拒绝。

安装发行包的方式、各命令的完整字段和文件布局见[入门](docs/getting-started.md)与[命令行](docs/cli.md)。

## 文档

- [文档索引](docs/README.md)
- [安装与第一次运行](docs/getting-started.md)
- [概念](docs/concepts.md)：run、世界、主体、插件、profile、历史与完整点、观察
- [命令行](docs/cli.md)：`check`、`lock`、`run start/resume/status/result`
- [profile 与插件](docs/profiles-and-plugins.md)：profile 写法、插件清单、插件入口规范
- [数据与数据集](docs/datasets.md)
- [把问题变成实验](docs/experiments.md)
- [因果分支](docs/causal-branches.md)：控制变量、平行世界与可信边界
- [TypeScript 构建记录](docs/typescript-build.md)

已有的公开接口说明保留在 `typescript/docs/`： [公开 SDK 使用](typescript/docs/public-usage.md)、[插件配置](typescript/docs/plugin-config.md)、[模型上下文](typescript/docs/model-context.md)、[平台入口](typescript/docs/platform-entry.md)。

## Skill

供 agent 使用的操作说明入口在 [skill/SKILL.md](skill/SKILL.md)。它按同一套命令和 profile 描述如何检查、运行、读取和分叉。

## 许可证

MIT。见 [LICENSE](LICENSE)。
