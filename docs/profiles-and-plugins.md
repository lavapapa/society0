# profile 与插件

## profile 的结构

profile 是 `society-profile/v2` JSON。它不直接 import 插件，而是描述条目树；宿主按条目名解析包，按作用域安装。

```json
{
  "format": "society-profile/v2",
  "bundles": [],
  "patches": [
    {
      "insert": [
        {
          "id": "run",
          "name": "run",
          "group": true,
          "lifetime": "run",
          "config": [
            {"id": "sqlite", "name": "@society0/core/sqlite", "config": {"directory": "./run"}}
          ]
        }
      ]
    }
  ],
  "configSources": [],
  "lockfile": "./module-lock.json"
}
```

字段：

- `format`：固定为 `society-profile/v2`。
- `bundles`：可复用的条目集合，可被 patch 引用。
- `patches`：装配操作。`insert` 增加条目；按 `id` 的 `config` 覆盖某个条目的配置。后一个 patch 的 `config` 会替换整个配置对象，因此覆盖时要保留必填字段。
- `configSources`：配置来源列表。
- `lockfile`：模块锁路径，相对 profile 文件解析；命令行 `--lock` 可以覆盖。

条目字段：

- `id`：条目在 profile 内的稳定标识，patch 和命令输出用它定位。
- `name`：包名或 `@society0/core/<export>` 形式的导出子路径。
- `group`：`true` 时 `config` 是子条目数组。
- `lifetime`：`run`、`bootstrap`、`generation` 等作用域；组条目用它声明子条目的生命周期。
- `config`：该插件或组的配置。
- `disabled`：显式关闭条目；`check` 的 `resolved` 只列未关闭条目。

四个发行 profile 在 `typescript/profiles/`：

| profile | 用途 | 模型 |
| --- | --- | --- |
| `rule-sqlite.json` | 固定 proposal 序列、staged runtime、规则主体 | 不需要 |
| `event-sqlite.json` | 事件源驱动、事件时钟、规则主体 | 不需要 |
| `social-sqlite.json` | Social 主体、关系、传播、推荐、测量 | `driver-llm` + `model-pi` + `agent-runtime-pi` |
| `social-codex-sqlite.json` | 同一 Social 栈，provider 换成订阅入口 | 默认关闭账号授权 |

规则 profile 适合做确定性试跑、分支和测量基线；Social profile 需要显式凭据环境和模型授权，命令行默认返回 `MODEL_NOT_AUTHORIZED` 或 `CODEX_NOT_AUTHORIZED`。模型参数和凭据由运行环境提供，不写进 profile 或公开产物。

## 改 profile 的常用方式

覆盖单个条目：

```json
[{"id":"checkpoint-policy","config":{"every":2}}]
```

插入新条目：

```json
[{"insert":[{"id":"datasets","name":"@society0/extras/datasets","config":{"directory":"run.datasets"}}]}]
```

用 patch 文件：

```sh
node .build/cli/dist/main.js check --profile profiles/rule-sqlite.json \
  --patches patch.json --lock module-lock.json --base .build --json
```

会影响结果的配置变化应使用新 run 或显式分叉；不要修改已经发布完整点的 run 配置。

## Core 入口

Core 的公开导出在 `@society0/core/package.json` 的 `exports` 中。常用入口按职责分组：

**存储与运行**

- `sqlite`、`sqlite-bootstrap`、`sqlite-history`：SQLite 数据库、schema 初始化、历史段和完整点。
- `artifacts-local`、`attempts`、`execution-fence`：外部工件、Attempts authority、写入边界。
- `run-controller`、`activation`、`activation-mailbox`、`actor-eligibility`、`actors`：推进、激活、主体资格和主体表。
- `results`、`thread`、`observation`、`generation-views`、`metrics`：结果、Thread、只读观察、代际视图和指标。

**时间、proposal 与策略**

- `clock-ticks`、`clock-event`：tick 时钟和事件时钟。
- `proposal-sequence`、`proposal-events`、`runtime-staged`、`runtime-events`：固定序列、事件源、staged 和事件 runtime。
- `checkpoint-policy`、`termination`、`completion-strict`、`generation-binding`：完整点频率、未完成策略、完成判定和代际绑定。
- `random`：具名随机流，使用 `philox4x32-10/v1` 和显式 seed。

**信息、行动与规则**

- `information`、`exploration`、`perception`、`perception-actor`：信息查询、探索、感知来源。
- `public`、`public-self`、`moment-views`：公开文件和主体视角。
- `actions`、`action-files`、`driver-rule`、`rule-passive`、`plain`、`plain-init`：行动、行动文件、规则 driver、被动规则和普通主体机制。

**模型与 Agent**

- `driver-llm`、`model-pi`、`model-codex-subscription`：模型 driver 和 provider 入口。
- `agent-runtime-pi`、`actor-tools`、`model-credentials`、`model-replay`、`prompts`、`inference-defaults`：Agent 运行时、工具、凭据、离线模型行为和提示词默认值。

## Extras 入口

- `datasets`、`dataset-jsonl`：不可变数据集、分帧读取和 JSONL codec。
- `memory`、`memory-repository`、`memory-write-experience`：记忆服务、存储和写入策略。
- `vector-index`、`vector-providers`、`vector-exact`：向量索引、提供者和精确检索。
- `shell`、`workspace`：可选 shell 和 workspace 文件视图。

## Social 入口

- `social-repository`、`social-init`、`topology-edges`：领域仓库、初始化和网络拓扑。
- `social`、`propagation-neighbors`、`round-robin`：主体行动、邻居传播和轮流 proposal 来源。
- `social-perception`、`social-recommendations`、`social-ranking`、`social-semantic`：感知、推荐准备、排序和语义索引。
- `measurements`、`measurement-repository`、`measurement-every`、`social-measurement`：测量、存储、采样和排名比较。

Social 的 schema、配置和边界见 `typescript/packages/social/src/README.md`；装配前先确认所选入口需要的主体、仓库和事件处理器。

## 写一个插件

宿主入口是 `@society0/host/abi` 导出的真实 Context、Service 和 native required inject。一个可装配入口通常导出：

```ts
import Schema from '@society0/schemastery'
import type { ApiEntry, PluginMetadata } from '@society0/host/module-resolver'

export const Config = Schema.object({
  directory: Schema.string().required(),
})

export const metadata: PluginMetadata = {
  title: 'example',
  summary: 'example mechanism',
  configVersion: 1,
  configPolicy: [{ path: '/directory', mode: 'new-run', affectsResults: true }],
  scope: 'generation',
  allowedScopes: ['generation'],
  hostAbi: 'society0/host/1',
}

export const api: ApiEntry = {
  exportPath: './example',
  provides: [{ key: 'example', protocol: 'example', major: 1, capabilities: [] }],
  requires: [{ service: { key: 'database', protocol: 'database', major: 1, capabilities: [] }, optional: false, phase: 'module' }],
  slots: { provides: [], requires: [] },
}

export default function apply(ctx, config) {
  // 注册信息源、行动、事件处理器或初始化器
}
```

要点：

- `Config` 的默认值就是有效配置；`check` 和 `--inspect` 读同一份 schema。
- `api.requires` 声明服务依赖和安装阶段；`api.slots` 声明运行期槽位。不要让插件在服务未就绪时自行猜测装配顺序。
- 需要建表或索引的插件导出 migration；migration 用 `after` 声明依赖，宿主在根创建时收集。
- `scope` 和 `allowedScopes` 限定运行范围。run、bootstrap、generation 三层的职责不同：数据先决条件属于 run，初始成员属于 bootstrap，机制属于 generation。
- 包清单里的 `society.plugin.metadata` 指向 sidecar，`society.plugin.api` 指向导出声明。发布和锁都读这些字段。
- 入口可以是 default 函数、类、对象，或命名 `apply`；同时提供 default 和 `apply` 会产生歧义。

完整的字段目录见[插件配置](../typescript/docs/plugin-config.md)。安装、运行和分叉的调用方式见[公开 SDK 使用](../typescript/docs/public-usage.md)。可运行的消费者示例在 `typescript/tests/fixtures/`，两个独立贡献者分别在 `consumer/index.ts` 和 `jsdoc/index.js`。

## 选择插件

先从问题出发，不要从插件数量出发：

- 要固定顺序、可重复的基线：`rule-sqlite`，只改 proposal 和规则处理器。
- 要外部事件推动：`event-sqlite`，事件进入 `proposal-events`，由 `clock-event` 给时间。
- 要内容、关系、推荐：在规则骨架上加 Social 入口，必要时再接模型 driver。
- 要记忆或数据集：加 Extras 入口，并给存储目录和预算。
- 要测量：加 `measurements` 和 `measurement-repository`，用采样策略决定什么时候记录。

一次只改变一层机制，会让结果更容易解释。跨层同时改动时，用[因果分支](causal-branches.md)拆成多个平行世界。
