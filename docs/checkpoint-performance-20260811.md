# Society0 检查点性能与持久化审查（2026-08-11）

## 结论

`complete_step_v3` 将顶层 World 作为唯一恢复权威：`agents_data` 和
`environment_data` 各写一份，`observation_data` 只保留 step 与 step-flow
诊断；`step_metrics` 也只保留一份。环境快照会移除顶层 `state`，因为该状态
已经由 `environment_data.state` 保存。核心 `Environment.snapshot()` 接受
`include_state=False`，保存路径因此只遍历默认环境 state 一次；gzip/JSON 压缩在
`asyncio.to_thread` 中执行，临时文件完成 fsync 后仍由事件循环原子 rename，
marker、gzip 文件 SHA-256 和恢复时的完整校验保持不变。

保存阶段不再为核对 `checkpoint_id`/`step` 解压刚写出的 gzip 文件。身份字段由
同一份待写 payload 和 marker 预先构成；`resolve_checkpoint`/`load_checkpoint`
仍会在恢复路径校验 gzip 可读性、文件 SHA、id/step、World metadata、Agent
Thread manifest、Chroma manifest 和恢复后的 World。

压缩字节的 SHA-256 与大小由 gzip worker 包装的 raw writer 在写入过程中增量计算；
原子 rename 后保存路径只用 `stat` 核对大小，避免再次完整读取压缩文件。恢复路径
仍重新计算文件 SHA，并与 marker 严格比对。

## A/B 证据

### 已关闭只读工件：重复字段基线

工件位于产业仓既有 real-agent 证据目录，未读取 `.codex-tmp`，文件本身未修改：

`research/simulation/industry-chain-society0-v2/artifacts/real-agent-e2e/industry-2026-08-09/full-2/pytest/test_real_four_agent_parallel_0/multi/checkpoints/checkpoint_000000.json`

该文件为 4 个主体、31,184 字节的未压缩 JSON。顶层和
`observation_data` 同时各保存 4 个主体；`environment_data` 也同时出现，且
默认快照还把 World state 放在 `snapshot.state`。重复字段的紧凑 JSON 大小为：
observation agents 1,585 字节、observation environment 8,457 字节、snapshot
state 8,420 字节，合计 18,462 字节。为排除旧工件缩进差异，以下比较先将两份
结构都用紧凑 JSON（`separators=(',', ':')`）编码，再以 gzip level 6、`mtime=0`
压缩；旧结构为 30,090/2,681 字节（未压缩/gzip）。

对同一已关闭 JSON 做 v3 结构投影（保留顶层 World，移除 observation 的
`agents_data`/`environment_data`、`snapshot.state` 和重复的顶层 `metrics`）后，
为 11,584/2,441 字节，紧凑 JSON/gzip 分别减少 18,506/240 字节；该投影只用于
结构/大小对照，不覆盖原工件。压缩后绝对节省量受数据重复度和 gzip 字典影响，
不能从这个单一文件外推长期运行比例。

### 小型合成工件：同步压缩与线程压缩

在同一工作时段、同一 Python 环境，以 4 个 rule agent、plain environment、
嵌套状态和一个 `step_metrics` 构造 8 次样本。A 将同一 writer 临时改为事件
循环同步执行；B 使用当前 `asyncio.to_thread` 路径。每次都写临时文件、原子发布
marker，并用 `resolve_checkpoint` 与 `load_checkpoint` 恢复检查。

| 模式 | wall 均值 | wall 最大样本 | 进程 CPU 均值 | World gzip 大小 | 恢复一致性 |
| --- | ---: | ---: | ---: | ---: | --- |
| A 同步 | 2.108 ms | 4.811 ms | 2.003 ms | 648 B | 8/8 |
| B `to_thread` | 2.077 ms | 3.721 ms | 1.961 ms | 652 B | 8/8 |

这是小工件的机制与数量级证据，线程调度开销与 gzip 工作量接近，不能宣称绝对
加速。线程路径的价值是让大 World 的 JSON/gzip CPU 工作不阻塞主 async 事件
循环；正式长跑仍应按主体数、检查点频率和并发负载重新测量，并同时记录 wall、
process CPU、压缩字节数和恢复耗时。

## Chroma 是否做内容寻址

当前实现仍为每个 checkpoint 将活动 Chroma 目录复制到唯一的
`chroma_backups/step_<step>.<checkpoint_id>` 目录，并在目录内写带
`checkpoint_id`/`step`/`memory_required`/内容 SHA 的 `_checkpoint.json`。本轮没有
把它改成内容寻址，理由是安全边界尚未闭合：

小型 disk-mode 复测以 192 KiB 的关闭 fixture 连续保存同一 step 3 次，得到 3 个
独立目录、合计 590,544 字节（约 192 KiB/份加 manifest）；save wall 为
2.82–6.35 ms，均值 4.05 ms，低于 0.12 s。这个量级说明当前 Chroma 复制不是
本轮 World JSON/gzip 成本的主要瓶颈；长期运行仍应按实际向量目录大小记录每份
重复空间和 copy 耗时。

1. Chroma PersistentClient 的 SQLite/WAL 等目录是可变运行数据；“逻辑记忆未变”
   不能直接推出所有文件字节未变，必须先完成 flush、冻结目录并对全部文件（含
   文件名）做哈希。
2. 现有 manifest 同时承担不可变内容校验和本 checkpoint 的身份绑定。多个
   checkpoint 共用目录后，目录 manifest 不能再唯一携带每个 checkpoint 的
   id/step；若只改 marker 引用，会丢失这层绑定。
3. 恢复会原子替换磁盘 store 和 tmpfs runtime 两个目标，并在失败时回滚。直接
   symlink 或跨目录引用会破坏当前的 symlink/path-escape 防护，也会让 GC 与
   恢复并发难以证明。

可接受的后续设计是：建立不可变
`chroma_objects/<content_sha256>/` 内容目录；每个 marker 保存
`content_sha256` 以及 checkpoint-specific 的引用元数据；resolver 分别校验
内容哈希和 checkpoint id/step，restore 只复制到临时 staging 后再原子替换两个
运行目标；GC 采用 marker 可达性 mark-and-sweep，禁止 symlink。实施前至少需要
覆盖相同内容复用、内容变化分叉、marker/对象篡改、marker 发布前崩溃、GC 保留
仍被引用对象、tmpfs 双目标回滚等测试。

## 验证与边界

- focused：`PYTHONPATH=src pytest -q tests/primary/test_checkpoint_memory_pairing.py`
-  —— 48 项通过。
- full default suite：在本子任务改动完成、其他 agent 的 `agent_loop.py` 改动尚未
  触发浮点边界前为 313 项通过、12 项跳过（跳过项均为未启用的真实
  LLM/embedding endpoint e2e）。最后一次并发工作树复跑为 317 项通过、1 项失败、
  12 项跳过；唯一失败是其他 agent 改动导致的 `0.30000000000000004` 与测试期望
  `0.3` 的浮点格式差异，未触及 persistence/environment 文件。
- 该报告的 A/B 只覆盖小型 rule world；没有读取或修改运行中的
  `.codex-tmp` 文件，也没有把旧工件的屏幕/进程状态当成完成证据。
- Chroma 内容寻址仍是明确的后续设计项，当前每 checkpoint 复制目录的空间成本
  仍存在。
