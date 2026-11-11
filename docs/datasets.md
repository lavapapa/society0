# 数据与数据集

Society0 把数据分成三类：世界里的可变记录、创建 run 时的初始输入、以及不可变的数据集。三类数据的写入路径不同，读取方式也不同。

## world 初始输入

`--world` 文件在 `run start` 时传给根创建。格式是一个 JSON 对象，`actors` 是数组：

```json
{
  "actors": [
    {"id": "alice", "attributes": {"name": "Alice"}, "source": {"village": "north"}},
    {"id": "bob", "attributes": {"name": "Bob"}, "source": {"village": "south"}}
  ]
}
```

每条 actor 必须有非空 `id` 和 `source`；`attributes` 是主体自己的资料。`check --world` 会在临时目录执行一次同样的根创建，把表约束、外键、主体缺失和 `source` 缺失转成可修复的 `world` 项。

`--world` 只提供输入，是否写入主体表由 profile 的初始化插件决定：

- `@society0/core/actors` 的 `seed` 从根创建输入建立主体。
- `@society0/core/plain-init` 用配置里的 `initialActors` 建立普通主体。
- `@society0/social/social-init` 在 `actors.seed` 之后建立 Social 成员、关系和配对。

同一份 world 可以用于多次新 run；每次 run 仍然要有自己的目录和 lineage。

## 领域数据

插件在自己的初始化器或已接纳的写事务里写入领域表。规范写入器同步更新权威数据、当前投影和索引；外部正文放进 artifacts，由引用关联。

适合写进世界的数据：

- 主体、关系、群体、配对；
- 帖子、回复、反应、通知、投递；
- 测量记录、推荐输入、排名结果；
- 运行过程中会变化、需要按 revision 读取的记录。

不适合内联的数据：

- 大段正文、完整文章、日志；
- 一次性导入的批量记录；
- 不需要按行更新的文件。

这些内容用数据集或 artifacts 引用；不要把它们直接塞进 SQL 行或 profile。

## 数据集

`@society0/extras/datasets` 提供不可变数据集服务，`@society0/extras/dataset-jsonl` 提供 JSONL codec。数据集由一串 JSON 记录编码成分帧对象，存入 artifacts；SQL 表只保存数据集 id 和对象引用。

装配两个入口：

```json
[
  {"insert": [
    {"id": "dataset-jsonl", "name": "@society0/extras/dataset-jsonl", "config": {"maxRecordBytes": 1048576}},
    {"id": "datasets", "name": "@society0/extras/datasets", "config": {"directory": "run.datasets", "codec": "jsonl/v1"}}
  ]}
]
```

配置：

- `datasets.directory`：数据集对象的目录，默认 `run.datasets`。
- `datasets.codec`：codec id，默认 `jsonl/v1`。
- `datasets.chunkBytes`、`datasets.maxFrameBytes`：编码分块和单帧上限。
- `datasets.maxPageItems`、`datasets.maxReadBytes`：分页条数和单次读取字节上限。
- `dataset-jsonl.maxRecordBytes`、`dataset-jsonl.chunkBytes`：单条记录和编码分块上限。

服务接口：

- `datasets.prepare(rows, options)`：把 `AsyncIterable<Json>` 编码为分帧对象，返回 `ArtifactRef`。`options` 包含 `maxRecordBytes`、`frameBytes`、`maxPendingBytes` 和 `signal`。
- `datasets.attach(tx, id, object)`：在一个已接纳的 SQL 写事务里登记数据集 id 和对象引用。
- `datasets.describe(id, view)`：返回记录数、原始字节数和 codec。
- `datasets.page(id, view, request)`：按游标读取一页，`request` 包含 `maxItems`、`maxResponseBytes`、可选 `cursor`。
- `datasets.readPayload(ref, range, signal)`：按原始字节范围读取正文，返回 `Uint8Array` 流。
- `datasets.get(ref, {maxBytes, signal})`：读取单条完整记录；超过 `maxBytes` 返回 `DATASET_RECORD_REFERENCE`。

典型流程是在插件初始化器里准备数据，然后在根创建的写事务中登记：

```js
const object = await ctx.datasets.prepare(rows(), {
  maxRecordBytes: 65536,
  frameBytes: 1048576,
  maxPendingBytes: 262144,
  signal,
})
// tx 必须是已接纳的写事务
await ctx.datasets.attach(tx, 'events-2026', object)
```

`rows()` 是 `async function*`，每次 `yield` 一个 JSON 值。准备阶段按帧编码；分页和按范围读取只解压与请求范围相交的帧。大记录以引用返回，分页响应只带引用元数据，正文用 `readPayload` 读取。

## 数据集的使用边界

- 数据集只追加，不修改。需要修正数据时建立新的数据集 id，不要覆盖原对象。
- 同一个 run 内的数据集对象通过 id 引用；分叉时对象引用随完整点保存。保留源 run 和目标 run 的 artifacts 与 lineage 目录，直到确认目标不再需要这些对象。
- `prepare` 的 `frameBytes` 不能超过 `maxFrameBytes`，单条记录不能超过 `maxRecordBytes`，分页条数不能超过 `maxPageItems`。
- 超限返回明确错误：`DATASET_FRAME_BUDGET`、`JSONL_RECORD_BUDGET`、`DATASET_PAGE_BUDGET`、`DATASET_READ_BUDGET`。
- 数据集内容和 SQL 世界数据是两条读取路径。要让主体通过信息查询看到数据，需要插件把数据集转成信息源或公开表，而不是让主体直接读 artifacts。

## 数据进入实验的顺序

1. 用 world 文件固定主体和初始属性。
2. 用初始化插件建立领域成员、关系和群体。
3. 用数据集导入不随运行变化的大批量记录。
4. 用 proposal 或事件源决定这些数据在什么时间被感知和行动。
5. 用测量插件记录结果，而不是在外部脚本里手工拼接。

数据准备和实验设计的关系见[把问题变成实验](experiments.md)。需要固定输入做对照时，见[因果分支](causal-branches.md)。
