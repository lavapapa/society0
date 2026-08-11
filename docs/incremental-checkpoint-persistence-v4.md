# Checkpoint v4：按持久化语义分层的增量检查点

## 1. 目标与非目标

v4 要把检查点写入成本从“重新编码累计 World”改为：

```text
O(本 Tick 已提交的新增事实 + 本 Tick 改写的可替换值 + 有界清单元数据)
```

这里的“有界”指清单只记录本 checkpoint 新发布的组件及父 checkpoint 标识，不随完整历史重复列举所有段。恢复某个 Tick 可以读取从最近物化基点到目标 Tick 的若干增量；恢复成本与需要重放的增量相关，不属于检查点写入热路径。

v4 不在保存时扫描、复制、比较或散列完整 World 来发现变化，也不为 v3 保留读取兼容层。产业模型、实验 runner 和当前运行工件不在本次改动范围内。

## 2. 四类持久化语义与声明 API

状态字段在环境或 Agent 的 JSON Schema 属性中声明 `persistence`：

```python
{
    "properties": {
        "cash": {"type": "number", "persistence": {"kind": "replaceable"}},
        "trades": {
            "type": "object",
            "persistence": {"kind": "append_only_map"},
            "additionalProperties": {"type": "object"},
        },
        "audit_log": {
            "type": "array",
            "persistence": {"kind": "append_only_list"},
            "items": {"type": "object"},
        },
        "tick_cursor": {
            "type": "integer",
            "persistence": {"kind": "transient"},
            "default": 0,
        },
    }
}
```

Env 作者通常不需要手写上面的 JSON。Society0 提供等价的声明构造器，
运行时代码仍是普通的 Python `dict` / `list` 操作：

```python
from society0 import (
    append_only_list,
    append_only_map,
    persistent_state_schema,
    replaceable,
    replaceable_map,
    transient,
)

INDUSTRY_STATE_SCHEMA = persistent_state_schema(
    clock=replaceable(),                  # 有界对象整体替换
    actors=replaceable_map(),             # 每个 actor_id 独立替换
    trades=append_only_map(),             # trade_id 对应不可变事实
    audit=append_only_list(),              # 有序不可变事实流
    runtime_index=transient(default={}),   # 恢复后重置
)

# Env 业务代码无需调用 checkpoint API 或手工 journal。
self.state["actors"][actor_id]["cash"] -= amount
self.state["trades"][trade_id] = trade
```

`replaceable()` 声明的对象必须是业务上有界的投影；任意深层写入最终只记录
该对象的 Tick 末值。`replaceable_map()` 表示动态对象表，深层写入
`table[id][...]` 只记录被改写的 `table[id]`，不会遍历或复制其他 entry。
整表赋值、删除或 `clear()` 会破坏这一上界，因此在写入前拒绝。需要更严格
类型校验时，可向构造器传 `schema`、`entry_schema` 或 `item_schema`；构造器
生成的仍是标准 Env `state_schema`，没有第二套恢复格式。

语义如下：

| 声明 | 允许的写入 | checkpoint 表示 | 恢复结果 |
| --- | --- | --- | --- |
| `replaceable` | create/set/delete；有界对象允许普通深层写入 | 本 Tick 最后一次已提交的有界锚点或 tombstone | 目标 Tick 的末值 |
| `replaceable` + `granularity=entry` | 动态 map 的 entry create/set/delete 与 entry 内深层写入；禁止整表改写 | 本 Tick 被修改 entry 的末值 | 目标 Tick 的逐 entry 投影 |
| `append_only_map` | 仅创建此前不存在的 key；同 Tick 重复 ID 失败 | `{id: fact}` 不可变段 | 合并截至目标 Tick 的所有段 |
| `append_only_list` | 仅 `append`/`extend` | 按确定顺序保存的不可变数组段 | 连接截至目标 Tick 的所有段 |
| `transient` | 任意运行时写入 | 不写入 | 新 Tick 或恢复后使用 schema `default`，无默认值时缺省 |

未声明字段不能悄悄回退到完整快照。v4 初始化时应报告具体路径并拒绝运行，以免一个新增字段把复杂度重新拖回 O(累计 World)。框架自身的固定小字段（step、环境类型、Agent 身份和类型）由显式的内置声明管理。

Chroma 不属于上述 JSON 状态树。每个 run 维持一个逻辑数据库，分支共享该库并通过元数据隔离；可配置的 tmpfs 运行镜像只在启动/关闭同步，不生成 checkpoint 版本。记忆记录必须包含 `created_step`、`visible_until_step`（Chroma 不接受 `None`，实现使用最大整数表示开放区间）、`branch_id`、`source_branch_id` 和写入 epoch。查询条件必须同时约束 Agent、分支可见谱系、目标 Tick 与 complete marker 已提交的 epoch。恢复旧 Tick 只改变查询视图，不复制、删除或回滚数据库；从旧 Tick 分叉时，新记忆写入新 `branch_id`，源分支记录保持不可变。

Agent Thread 继续作为独立、不可变的 JSONL/blob 证据。checkpoint 只引用该 Tick 已关闭 Thread 的不可变 manifest；Thread 正文不进入 World 状态段。

## 3. 写入时捕获变化

`World` 持有一个 `StateDeltaJournal`。每个 Tick 开始时创建未发布 journal；`DictProxy`、`ListProxy` 和少数 canonical writer 在实际修改底层容器的同一调用栈内，把规范化操作写入 journal。代理在 mutation 前解析声明并完成类型、重复 ID 与操作合法性校验；mutation 成功后，journal 才复制新增事实或命中的有界替换锚点。这样底层 list/dict 操作失败时不会留下幽灵 delta，也能在普通深层写入后捕获对象的最终值。

捕获顺序是单调的 `sequence`。同 Tick 对 replaceable 锚点的 create/update/delete 依次记录，发布前按锚点压缩为末值；同一个 entry 内多次 dict/list 修改只保留最终 entry。append-only 操作保持原顺序且不压缩。重复 append-only map ID 和对已创建事实的深层修改都在底层 mutation 前失败，不能等 checkpoint 扫描后才发现。

代理只包装已通过公共状态 API 暴露的容器。读操作不建立全树代理、不扫描历史。框架内部确需绕过代理的写入路径必须改为 canonical writer；直接替换 `World.agents_data` 或 `environment_data["state"]` 仅允许在初始化和恢复阶段，并在该阶段关闭 journal。

现有 StateChangeEvent 可继续承担审计日志，但不能作为 checkpoint delta 的来源：事件写入可能失败或被裁剪，且其 `old_value` 会造成不必要复制。journal 与底层状态写入同源，二者分别服务于恢复和审计。

## 4. v4 文件布局与 manifest

```text
checkpoints/
  v4/
    segments/<sha256>.json.gz       # 不可变 append 段；按内容寻址
    replacements/<checkpoint-id>.json.gz
    manifests/<checkpoint-id>.json
    complete/step_000123.json       # 唯一发布点
agent_threads/manifests/...
chroma_store/                       # 每个 run 逻辑单库；branch 由元数据隔离
```

`manifest` 至少包含：

```json
{
  "checkpoint_version": "complete_step_v4",
  "checkpoint_id": "...",
  "run_id": "...",
  "branch_id": "main",
  "step": 123,
  "parent_checkpoint_id": "...",
  "replacement_file": "replacements/...json.gz",
  "replacement_sha256": "...",
  "new_segments": [{"path": "segments/...json.gz", "sha256": "..."}],
  "thread_manifest": {"path": "...", "sha256": "..."},
  "memory_view": {"branch_id": "main", "visible_step": 123},
  "state_sha256": "链式状态摘要",
  "created_at": 0
}
```

`state_sha256 = H(parent.state_sha256, canonical(replacements), ordered(segment hashes), thread manifest hash, memory view, annotations)`。它证明发布链没有被重排或替换，不要求保存时重读历史段。每个组件先写临时文件、`fsync`、原子改名；最后才原子写入 complete marker。只有 marker 指向的 manifest 可恢复。

## 5. Tick、失败、取消和进程中断

一个 Tick 的状态依次经过 `runtime delta -> sealed delta -> published checkpoint`：

1. Tick 开始创建 runtime delta。
2. 底层写入同步记录操作。
3. Tick 成功后 seal；此后业务代码不能再修改该 delta。
4. checkpoint writer 写组件和 manifest。
5. complete marker 原子发布后，parent 才推进。

事务不存在或现有业务事务只覆盖部分节点时，失败 Tick 不尝试反向修改内存 World。运行终止，runtime delta 直接丢弃；恢复仍从最后完整 marker 建立新 World。取消与进程中断遵循同一边界。marker 写入前的文件都是孤儿；marker 原子替换成功后，即使调用方随后收到异常，该 checkpoint 仍按已发布处理。

每个 `PersistenceManager` 最多允许一个未完成 checkpoint。第二个保存请求应等待第一个完成（背压），不能并发消费两个 sealed delta，也不能覆盖 parent。

## 6. 恢复、任意 Tick 与分叉

恢复从目标 complete marker 开始，沿 parent 链向前找到最近的物化基点，校验每个 manifest 与组件哈希，然后按 `(step, sequence)` 应用 replacement 和 append 段。缺段、内容损坏、parent 断裂或链式摘要不一致都使目标 checkpoint 不可恢复；“latest”解析应跳过损坏目标并尝试更早完整点。

分叉在同一 run 内创建新的 `branch_id` 和分支根 marker，引用源 checkpoint 的已验证不可变链，不复制段内容。之后只在新分支目录发布 marker，并把 Chroma 查询谱系记录为“源分支截至 fork step + 新分支自身截至目标 step”。源 complete marker、Thread 和记忆均不可修改。跨 run 恢复仍创建新的运行目录，并从来源 v4 marker 读取已验证状态。

## 7. GC 与保留策略

GC 只在没有 checkpoint writer 时运行。它先从所有 complete marker、活动分支根和显式保留点标记可达 manifest，再标记 replacement、segment 和 Thread manifest；未被标记且早于 GC 启动时刻的组件才可删除。Chroma 记录不随 checkpoint GC 删除；删除分支需显式执行，并保留源谱系仍引用的记忆。第一阶段只实现孤儿组件清理，完整保留策略留在后续阶段。

## 8. 复杂度与空间上界

设本 Tick 新增事实总字节为 `A_t`，被改写的有界 replaceable 锚点末值总字节为 `R_t`，固定 manifest 大小为 `M`：

```text
写入时间：O(A_t + R_t + M)
写入临时内存：O(A_t + R_t + M)，流式编码时可降到单条记录上界
新增磁盘：O(A_t + R_t + M)
```

append-only 历史总量 `H_t` 和 replaceable map 中未修改的 entry 数量不出现在保存上界中。若一个 `replaceable()` 对象自身持续无界增长，`R_t` 仍会变大；调用方应改用 `replaceable_map()`，或拆成 append-only 事实与紧凑投影。性能门禁构造不断增长的历史和固定 delta，统计 writer 接收的记录数/字节数，并用峰值 RSS 观察是否出现隐藏全量副本；墙钟时间仅作辅助，因为噪声不能单独证明复杂度。

## 9. 实现状态与后续优化

### 阶段 A：最小垂直切片

- [x] 定义声明解析、`StateDeltaJournal` 和确定性操作格式。
- [x] 让环境 `DictProxy`/`ListProxy` 在写入点记录 replaceable 与 append-only delta。
- [x] 实现 v4 segment、replacement、manifest、complete marker 与任意 Tick 恢复。
- [x] 覆盖失败前不发布、临时态丢弃、损坏/缺段检测和固定 delta 性能门禁。

### 阶段 B：完整 World 与运行时生命周期

- [x] 覆盖 Agent state/properties/reminders 和框架固定字段。
- [x] 在 `Society0.run` 与 `SimEngine` 中接入 begin/seal/abort，保证最多一个 sealed delta。
- [x] 接入 Thread immutable manifest，补齐取消、marker 前后故障和背压。
- [x] 加入随机多 Tick 状态机测试与恢复等价验证。

### 阶段 C：记忆与分叉

- [x] 为 Memory 写入补 `created_step/visible_until_step/branch_id/source_branch_id/write_epoch_id`。
- [x] 所有 retrieve/inspect/export 路径强制目标 Tick 与已提交 epoch 可见性过滤。
- [x] 实现从任意完整 checkpoint 分叉、源谱系查询与互不污染测试。
- [x] 删除按 checkpoint 复制 Chroma 的 v3 路径。

### 阶段 D：GC、物化与基准

- [x] 实现跨分支可达性 GC，清理孤儿 manifest、replacement、segment 与 Thread manifest；Chroma 不由 checkpoint GC 删除。
- [ ] 可选的周期性物化基点留作恢复延迟优化；v4 root 已提供初始物化点，增量写入正确性与复杂度不依赖该优化。
- [x] 运行 unit、primary、恢复、Thread、Chroma、E2E 与历史增长基准，记录耗时、字节数、峰值 RSS 和失败注入结果。

### 9.1 2026-08-11 历史增长基准

固定每 Tick 三条记录（一个 replacement、一个 append-only map fact、一个 append-only list fact）和 256 字节可替换投影；历史从 100 增至 10,000 Tick，放大 100 倍，每档连续发布 100 次：

| 历史 Tick | 写入字节中位数 | 记录数中位数 | 历史组件读取 | 保存耗时中位数 | 额外 `ru_maxrss` |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 1,632 B | 3 | 0 | 0.902 ms | 416 KiB |
| 1,000 | 1,635 B | 3 | 0 | 0.756 ms | 48 KiB |
| 10,000 | 1,637 B | 3 | 0 | 0.891 ms | 0 KiB |

历史放大 100 倍时，写入字节中位数比为 1.003，记录数比为 1.0，保存热路径读取历史组件为零。4,096 字节可替换投影的正向对照把写入中位数从 1,638 B 提高到 2,519 B，说明成本随本 Tick 的 `R_t` 增长。原始报告保存在 `benchmarks/results/v4-history-ab.json`；墙钟最大值仅记录，不作为复杂度证明。

## 10. 当前设计决定

声明与 journal 已成为 recoverable checkpoint 的唯一变化来源。实现不会把整个 state 递归包装为常驻代理，也不会在读取时计算差异。Chroma 已切换为单库多 Tick/分支视图；诊断 gzip 仍可在运行终止时生成，但标记为不可恢复，不能发布 complete marker。
