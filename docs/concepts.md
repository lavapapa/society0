# 概念

## run

run 是一次仿真的执行单位。一个 run 有自己的目录、run id、lineage authority、SQLite 数据库和历史。它由 profile 装配，由 `run-controller` 推进。

run 的推进以 tick 为单位。每个 tick 从 proposal 源取出工作，按 phase 激活主体，等待行动回执，再发布完整点。`run status` 的 `frontier` 描述已经发布到哪里；`run result` 的 `result.cut` 是最后一个完整点。

同一份 profile 可以创建多个 run，只要目录和 lineage 不同。创建要求目标目录尚未使用，这样每个 run 的起点可以追溯。

## 世界

世界是主体共享的环境。插件向世界登记表、索引和信息源；主体通过信息查询和感知读取它，通过行动修改它。

世界不只是“当前数据库”。规范写入器在一个 SQL 事务里同时更新权威数据、当前投影和必要的索引；外部正文放在 artifacts 里，由引用关联。历史文件记录每次变化，roots 和完整点让这些变化可以重建。

`--world` 文件是创建 run 时的输入，格式为：

```json
{
  "actors": [
    {"id": "alice", "attributes": {"name": "Alice"}, "source": {"village": "north"}},
    {"id": "bob", "attributes": {"name": "Bob"}, "source": {"village": "south"}}
  ]
}
```

每条 actor 都要有非空 `id` 和 `source`。是否用这些 actor 建立初始成员，取决于 profile 里启用的初始化插件：`@society0/core/actors` 的 `seed` 和 Social 的 `social.seed` 会读取根创建输入；只装 `plain-init` 的 profile 使用 profile 自带的 `initialActors`。

## 主体

主体（actor）是有 id、属性和来源记录的参与者。主体本身不包含行为；行为由 driver 和插件决定。

- `driver-rule` 按规则处理器执行固定行为，不读取模型。
- `driver-llm` 走完整 Agent 循环：感知、Thread、工具或行动选择、回执、完成判定。
- 规则和模型 driver 都通过 Activation 接收同一个 `actorId`、`momentId` 和输入，不共享隐藏状态。

主体可以属于微观群体，例如 Social 的 `pairingGroups`、拓扑边或推荐来源。群体关系是普通世界数据，参与感知和行动，不改变 run 的生命周期。

## 插件

插件是机制的装配单位。一个插件可以：

- 通过 `Config` 声明配置字段和默认值；
- 通过 `metadata` 声明作用域、配置更新方式和是否影响结果；
- 通过 `api` 声明导出的服务、需要的服务、槽位和 migration 依赖；
- 在 `apply` 里注册信息源、行动、事件处理器、初始化器或其他服务。

Core 提供运行所需的基础插件；Extras 提供数据集、记忆、向量和 workspace；Social 提供关系、内容、传播、推荐和测量。插件之间通过服务和槽位连接，而不是直接 import 彼此的内部模块。

装配顺序由宿主的 `compose` 处理：先准备共享存储和 schema，再安装服务，最后挂载 generation 级机制。一个插件声明了数据前置条件，不代表它可以跳过 schema 初始化。

## profile

profile 是一份 `society-profile/v2` JSON，描述要装配哪些插件、每个插件的配置，以及锁定哪些包。它不是一个可直接执行的脚本；`society0 check` 会解析它，`run start` 会编译它。

profile 的 `patches` 是装配操作。常见的 `insert` 把一组条目加进 run、bootstrap 或 generation 组；按 `id` 给出的 patch 覆盖某个条目的配置。`bundles` 用于引用可复用的条目集合，`lockfile` 指向模块锁。

同一份 profile 可以创建多个 run，但会影响结果的配置变更应使用新 run 或显式分叉；不要在现有 run 上改写已经发布的配置。

## 历史与完整点

历史保存 run 的变化段、roots 和完整点。完整点是某个推进边界上可信的状态：它覆盖主体、领域数据、Thread、结果和声明过的外部工件。

发布完整点之前，规范写入器要完成该 tick 的必要工作并等待回执。未完成的激活、失败的写入或未知的事务结果不会被当作完整状态发布。`termination.onIncomplete` 决定遇到未完成激活时暂停还是继续策略。

恢复和分叉都以完整点为起点。`run resume` 激活已有历史，跳过初始化 seed；它不会重放未完成的执行。`run result` 读取的是已发布的最后一个完整点。

历史用于分析时是只读的。Observation 可以查询当前投影和状态，但不会修改世界。

## 观察

Observation 是读取当前 run 的接口：它按已接纳的只读视图执行查询，返回带 revision 的快照、状态和诊断。它适合读取主体、行动回执、Social 内容和当前完整点。

观察和结果是两条路径：

- 结果（Results）保存测量和实验产出，按 run 和完整点组织。
- 观察（Observation）读取当前世界投影，用于运行中检查和调试。

如果要分析已经结束的 run，优先读 `run result` 和 Results；如果要在运行中检查数据库，使用 Observation 的只读视图。两者都不会让 run 前移。

## 时间与顺序

时间由 profile 选择。`clock-ticks` 用递增 tick，`clock-event` 用事件携带的 `time` 和 `offset`。proposal 源决定下一批激活：`proposal-sequence` 读取固定序列，`proposal-events` 等待事件输入。

Phase 表示业务顺序，`Phase.capacity` 或 `Runtime.capacity` 表示独立阶段可以并行的程度。提供方请求并发是另一层预算，不改变业务顺序。完整点只在所需工作完成后发布。
