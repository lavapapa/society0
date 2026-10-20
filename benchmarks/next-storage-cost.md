# Next 存储成本归属

这个实验复用[检查点基线](next-checkpoint-baseline.md)的真实 StageStore 创建、事务、完整点、打开、只读物化、恢复与继续运行路径，解释表数和行布局如何影响成本。计时与归属使用独立进程；原始运行目录保留在指定仓外目录。

## 一、运行

先运行小 probe，确认空间与原生 API，再扩大工作量。下列命令分别选择两个新目录：

```sh
uv run --no-sync python -m benchmarks.core_next_storage_cost \
  --output /absolute/task-evidence/small-timing --mode timing \
  --history-rows 16 --steps 2 --delta-rows 2
uv run --no-sync python -m benchmarks.core_next_storage_cost \
  --output /absolute/task-evidence/small-profile --mode profile \
  --history-rows 16 --steps 2 --delta-rows 2
```

每个进程保存 `report.json` 和 source、readonly、restored、continued 四类目录。报告包含实际导入路径、源码身份、Python/APSW/SQLite 版本、完整点逐步墙钟/CPU、磁盘字节和恢复核对结果。默认无额外工件；`--artifacts-per-step` 可恢复基线的工件负载。

实验优先选择以下两个轴，每个配置交替执行 timing/profile、重复数次：

- **表数**：固定活动集合和增量，通过 `--extra-tables 0/32/200` 增加空闲业务表。三个基础表照常运行。
- **大行布局**：`--position-body-bytes 65536/262144` 给每个活动主体保留不变的伪随机正文。`--position-body-layout inline` 与数值投影同表；`detached` 将相同正文保存在独立主键表。每步更新数值、追加事实，正文逐字节验证。两种布局保留相同业务信息。

历史行数、活动行数、步数、每步增量和事实正文大小均有独立参数。本地使用小负载；百万行根状态放在服务器，并遵循工作区的运行资产约定。

## 二、口径

`timing` 路径保留阶段边界的轻量计时，没有 patch、VFS 切换或原生 Session 采样。`profile` 在独占 worker 内临时装配仪器，计数随实际调用发生：

| 报告项 | 实际观测边界 |
|---|---|
| `schema_*`、`tables_*` | 现有 `_schema`、`_tables` 调用及耗时；返回表数单独计数 |
| `schema_query_count`、`table_info_count` | APSW 执行 trace 中的 schema 查询、PRAGMA 实际执行次数 |
| `session_rebuild_*`、`session_create_*`、`session_attach_*` | `_start_session`、原生 Session 构造和 attach 调用 |
| `session_memory_max_bytes` | 每次导出前读取原生 `apsw.Session.memory_used`；跨样本取最大值 |
| `export_*`、`compression_write_*`、`compression_finish_*` | 原生 `changeset_stream`，sink 实际调用次数和未压缩字节；压缩 write 与结束封帧单独计时 |
| `descriptor_publish_*`、`manifest_publish_*`、`artifact_sync_*` | 现有 `_json` 发布与 `_artifacts` 引用同步；保留全部真实写盘 |
| `descriptor_read_*` | 实际完整 read_text 调用次数、耗时及不可变描述符文件字节 |
| `python_fsync_*`、`directory_sync_*` | 保留真实 fsync，按 fd 类型区分目录；目录同步包含在 Python fsync 中 |
| `sqlite_sync_*` | 复用现有 `SyncVFS/SyncFile.xSync`，保留 SQLite 的真实同步 |
| `backup_*` | 现有 `_backup` 调用、耗时和实际输出文件字节 |
| `apply_*`、`apply_read_*` | 保留 `Changeset.apply(stream.read, connection, ...)`；计数读取回调次数及解压后字节 |
| `compressed_read_*` | Zstd 输入端实际 read 次数及压缩字节，包含 EOF 读取 |
| `validate_references_*` | writer open 的完整链引用验证段 |

分段耗时具有包含关系：`tables` 包含 `schema`，Session 重建包含原生创建和 attach，export 包含 sink 的压缩，apply 包含读取/解压，backup 包含其内部同步。writer open 验证并固定本实例的业务表名；后续完整步骤重建 Session 沿用这些表名，因此 complete 段的 schema/table_info 计数为零，attach 仍随表数增长。新 open 和可写 restore 仍执行表与主键验证。SQLite 同步还可能落在 apply 等段内。报告中的分段不可直接相加。Session 内存是原生跟踪状态；进程 RSS 为包含导入和验证的整进程高水位。

`instrumentation.bookkeeping_seconds` 实测数字累加函数的部分开销，作为下界；wrapper、执行 trace、原生内存采样和 Python VFS 的额外成本用同配置纯计时进程比较。两类运行的总耗时、每段绝对值和比值分别保留。调度和文件缓存噪声可能使 profile 更快，负差值保留原值。cProfile 耗时不进入加速结论。

## 三、验证

验证在测量的 complete/open/restore 段外逐行检查历史、事实、活动投影和大行正文，恢复后继续提交并重新物化该点。报告保存短标识和数字，正文属于原始运行工件；仪器不复制业务状态、不计算 hash。

```sh
uv run --no-sync python -m pytest \
  tests/experiments/test_core_next_storage_cost.py \
  tests/experiments/test_core_next_checkpoint_baseline.py \
  --basetemp /absolute/task-evidence/new-test-directory \
  -o tmp_path_retention_policy=failed
```

测试通过额外执行一次原生 PRAGMA，确认访问数来自真实 SQL；禁用 Session 导出会使实际恢复消费者失败。故障注入与异步 callback 拒绝都检查 patch、默认 VFS 的退出。成功测试的临时世界由 pytest 清理；实验原始工件持续保留。

`CostProbe` 是同步的进程级实验工具，作用域内不可 await，执行期间保持 worker 独占。文件系统缓存没有清除；cold_open 指新 writer 连接。伪随机大正文在主体间重复，事实正文保留基线的可压缩 `x`；这些结果用于存储机制决策，真实语料压缩率和产业经营效果另行验证。
