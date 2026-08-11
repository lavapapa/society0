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
        "cash": {"type": "number", "persistence": "replaceable"},
        "trades": {
            "type": "object",
            "persistence": "append_only_map",
            "additionalProperties": {"type": "object"},
        },
        "audit_log": {
            "type": "array",
            "persistence": "append_only_list",
            "items": {"type": "object"},
        },
        "tick_cursor": {
            "type": "integer",
            "persistence": "transient",
            "default": 0,
        },
    }
}
```

语义如下：

| 声明 | 允许的写入 | checkpoint 表示 | 恢复结果 |
| --- | --- | --- | --- |
| `replaceable` | create/set/delete；map 内部可逐键更新 | 本 Tick 最后一次已提交的值或 tombstone | 目标 Tick 的末值 |
| `append_only_map` | 仅创建此前不存在的 key；同 Tick 重复 ID 失败 | `{id: fact}` 不可变段 | 合并截至目标 Tick 的所有段 |
| `append_only_list` | 仅 `append`/`extend` | 按确定顺序保存的不可变数组段 | 连接截至目标 Tick 的所有段 |
| `transient` | 任意运行时写入 | 不写入 | 新 Tick 或恢复后使用 schema `default`，无默认值时缺省 |

未声明字段不能悄悄回退到完整快照。v4 初始化时应报告具体路径并拒绝运行，以免一个新增字段把复杂度重新拖回 O(累计 World)。框架自身的固定小字段（step、环境类型、Agent 身份和类型）由显式的内置声明管理。

Chroma 不属于上述 JSON 状态树。它维持每个 run/branch 一个数据库，记忆记录必须包含 `created_step`、`visible_until_step`（开放区间可为空）、`branch_id` 和 `source_branch_id`。查询条件必须同时约束 Agent、分支可见谱系和目标 Tick。恢复旧 Tick 只改变查询视图，不复制或回滚数据库；从旧 Tick 分叉时，新记忆写入新 `branch_id`，源分支记录保持不可变。

Agent Thread 继续作为独立、不可变的 JSONL/blob 证据。checkpoint 只引用该 Tick 已关闭 Thread 的不可变 manifest；Thread 正文不进入 World 状态段。

## 3. 写入时捕获变化

`World` 持有一个 `StateDeltaJournal`。每个 Tick 开始时创建未发布 journal；`DictProxy`、`ListProxy` 和少数 canonical writer 在实际修改底层容器的同一调用栈内，把规范化操作写入 journal。journal 根据字段声明立即判定操作是否合法，并复制本次写入值，不读取同字段的完整历史。

捕获顺序是单调的 `sequence`。同 Tick 对 replaceable 字段的 create/update/delete 依次记录，发布前可按路径压缩为末值；append-only 操作保持原顺序且不压缩。重复 append-only map ID 在写入时失败，不能等 checkpoint 扫描后才发现。

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
chroma_store/                       # 每个 branch 单库，不按 checkpoint 复制
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

`state_sha256 = H(parent.state_sha256, canonical(replacements), ordered(segment hashes), thread manifest hash, memory view)`。它证明发布链没有被重排或替换，不要求保存时重读历史段。每个组件先写临时文件、`fsync`、原子改名；最后才原子写入 complete marker。只有 marker 指向的 manifest 可恢复。

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

分叉创建新的 `run_id/branch_id` 和根 manifest，根 manifest 引用源 checkpoint 的已验证不可变段，不复制段内容。之后只在新分支目录发布 manifest，并把 Chroma 查询谱系记录为“源分支截至 fork step + 新分支自身截至目标 step”。源 complete marker、Thread 和记忆均不可修改。

## 7. GC 与保留策略

GC 只在没有 checkpoint writer 时运行。它先从所有 complete marker、活动分支根和显式保留点标记可达 manifest，再标记 replacement、segment 和 Thread manifest；未被标记且早于 GC 启动时刻的组件才可删除。Chroma 记录不随 checkpoint GC 删除；删除分支需显式执行，并保留源谱系仍引用的记忆。第一阶段只实现孤儿组件清理，完整保留策略留在后续阶段。

## 8. 复杂度与空间上界

设本 Tick 新增事实总字节为 `A_t`，被改写的 replaceable 末值总字节为 `R_t`，固定 manifest 大小为 `M`：

```text
写入时间：O(A_t + R_t + M)
写入临时内存：O(A_t + R_t + M)，流式编码时可降到单条记录上界
新增磁盘：O(A_t + R_t + M)
```

append-only 历史总量 `H_t` 不出现在保存上界中。若一个 replaceable 字段本身是无界大 map，整字段替换仍会使 `R_t` 变大；调用方应把它声明为逐键 replaceable map，或重构为 append-only 事实与紧凑索引。性能门禁构造不断增长的历史和固定 delta，统计 writer 接收的记录数/字节数，并用峰值 RSS 观察是否出现隐藏全量副本；墙钟时间仅作辅助，因为噪声不能单独证明复杂度。

## 9. 分阶段 TODO

### 阶段 A：最小垂直切片

- [ ] 定义声明解析、`StateDeltaJournal` 和确定性操作格式。
- [ ] 让环境 `DictProxy`/`ListProxy` 在写入点记录 replaceable 与 append-only delta。
- [ ] 实现 v4 segment、replacement、manifest、complete marker 与任意 Tick 恢复。
- [ ] 覆盖失败前不发布、临时态丢弃、损坏/缺段检测和固定 delta 性能门禁。

### 阶段 B：完整 World 与运行时生命周期

- [ ] 覆盖 Agent state/properties/reminders 和框架固定字段。
- [ ] 在 `Society0.run` 与 `SimEngine` 中接入 begin/seal/abort，保证最多一个 sealed delta。
- [ ] 接入 Thread immutable manifest，补齐取消、marker 前后故障和背压。
- [ ] 加入随机多 Tick 状态机测试与恢复等价验证。

### 阶段 C：记忆与分叉

- [ ] 为 Memory 写入补 `created_step/visible_until_step/branch_id/source_branch_id`。
- [ ] 所有 retrieve/inspect/export 路径强制目标 Tick 可见性过滤。
- [ ] 实现从任意完整 checkpoint 分叉、源谱系查询与互不污染测试。
- [ ] 删除按 checkpoint 复制 Chroma 的 v3 路径。

### 阶段 D：GC、物化与基准

- [ ] 实现孤儿清理和基于可达性的 GC。
- [ ] 以可配置间隔生成物化基点；物化在后台执行且不改变 checkpoint 写入复杂度门禁。
- [ ] 运行 unit、primary、恢复、Thread、Chroma、E2E 与历史增长基准，报告耗时、字节数、峰值 RSS 和失败注入结果。

## 10. 当前设计决定

最小实现先让声明与 journal 成为唯一变化来源，再写 v4 文件格式。不会把整个 state 递归包装为常驻代理，也不会在读取时计算差异。Chroma 的单库多 Tick 视图需要改写现有按 checkpoint 备份/恢复合同，安排在 World 增量链稳定之后，但它属于 v4 完成标准，不能继续把目录复制称为最终实现。
