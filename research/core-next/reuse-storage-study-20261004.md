# 存储机制与成熟方案复用研究

本研究日期为 2026-10-04，代码定位为 `0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0` 工作树中的现行实现。任务是重新判断经典问题与成熟解法的边界。本轮仅阅读源码和官方资料，未修改产品、安装依赖、执行性能试验或调用模型。下文“建议”均为待决策方案，现有测量引用保留原报告身份。

## 一、问题定位

当前架构已经复用数据库与原生编码器，但仍拥有多种正文布局及恢复发布协议。评估重点应是能删除多少长期维护责任，同时保留完整信息、步骤恢复和按需读取。

### 1.1 已复用与自有部分

[storage.py](../../src/society0/kernel/storage.py) 的 transaction/read、Session、backup 对应嵌入式关系数据库的事务、短快照、变更捕获和物理备份。SQL 索引与缓存由 SQLite 维护；按表 revision、单规范 writer、完整 step 身份及外部工件登记属于应用协调。complete 将原生 changeset 流耐久写入后发布独立 JSON 描述符；restore 从根数据库应用完整链，并复制或硬链接不可变工件。因此当前没有自写 WAL，但拥有一个应用级恢复目录协议，包含链校验、发布顺序、分叉、导出与孤儿回收。

[datasets.py](../../src/society0/kernel/datasets.py) 把任意 JSON 记录串接为逻辑字节流，SQLite records 表保存 ordinal→原文起点/长度，blocks 表保存独立 64KiB zstd 帧。这是“有记录索引的分帧压缩文件”，SQLite 在此承担容器目录功能。自有范围定位、跨块拼接、单块缓存及分页预算形成了实际格式维护责任，即使文件外壳是 SQLite，该格式也有自定义部分。

[_json_chunks.py](../../src/society0/kernel/_json_chunks.py) 使用 RapidJSON 同步 push 编码，压缩交给 zlib；自行管理小值预读、ThreadPoolExecutor、有界 future 队列、按序提交与异常排空。[threads.py](../../src/society0/kernel/threads.py) 将事件元数据与每条消息的独立压缩块写入同一 SQL 事务，消息引用和请求水位避免重复保存全部历史请求。前者属于可替换的压缩管线，后者属于仿真证据语义。

[workspace.py](../../src/society0/kernel/workspace.py) 与 [原生桥](../../native/society0-filesystem/src/lib.rs) 已使用 Bashkit OverlayFs 处理文件操作，SQL 保存目录与文件元数据，变更文件内容成为不可变 artifact。现有 callback 的 read 和 copy-up 仍可能完整读取一个文件，保存变更文件也持有整份 bytes。目录索引降低枚举成本，却没有自动消除巨文件局部修改的整文件成本。旧 `persistence.py` 已退役，现行讨论应以 StageStore 为准。

### 1.2 应明确分开的访问目标

热状态需要点查、条件查询、小事务和稳定业务索引；完整步骤需要一次跨插件的明确发布身份；冷数据需要顺序扫描、列投影或记录定位；模型历史需要按 Thread 顺序恢复完整原文。把这些目标统一为任意 JSON 大对象，会重新引入解析、重写与内存下界。把全部对象统一成列式表同样会增加插件的类型和 schema 负担。适当的共同层是身份、引用、事务与生命周期；物理表示可以按数据性质选择。

## 二、事务与恢复

SQLite 原生能力与当前代码契合度最高。是否进一步简化，应先决定“运行中短事务可见”与“完整步骤可恢复”是否都必须成立。

### 2.1 保留 SQLite/APSW，重新评估完成协议的必要复杂度

SQLite Session 针对一个连接捕获有显式主键的普通表，支持流式生成与应用。捕获 UPDATE/DELETE 时保存原行，流式输出不会消除这些原生内存；虚表及空主键行有明确限制。当前冻结 schema、规范连接和正文分离正是应对这些条件。建议保留，禁止再增加自写行级 undo、WAL 或另一套内存 MVCC。[SQLite Session](https://www.sqlite.org/sessionintro.html)、[APSW Session](https://rogerbinns.github.io/apsw/session.html)。

一个完整步骤直接对应一个数据库事务，能够删去部分 live/complete 双水位与逻辑恢复链，但读者在提交前看到上一完整点；模型请求、Thread 即时诊断及跨 await 的短事务可见性需要另作安排。当前短事务先发布 live、步骤结束再封存 Session，是为保留这些已验语义作出的选择。建议把这两种明确合同作架构比较，避免默认把任意多层提交视为必须。

APSW 的 patchset_stream 可以减少 UPDATE/DELETE 的旧值输出，却降低冲突识别能力，且捕获期仍保留原行。它适合“源身份和应用顺序严格受控”的专项比较，当前收益与失去的错误检测尚未测量，归为待验证。不要把 patchset 当作解决大行捕获内存的方案。

### 2.2 快照、备份和持久历史各有范围

原生 backup 可分批复制一致数据库；分批降低单次阻塞，最终仍形成数据库副本。SQLite snapshot API 依赖 WAL 中仍可取得的历史页，缺少长期历史存档语义，也需要特定构建能力。长读事务会限制 checkpoint 推进；WAL 官方要求参与进程位于同一主机。当前本地 current、远端不可变封存与 HTTP 观察的部署边界应保留。[Backup](https://www.sqlite.org/backup.html)、[snapshot_get](https://www.sqlite.org/c3ref/snapshot_get.html)、[WAL](https://www.sqlite.org/wal.html)。

当前 restore 重建新根即离线压实，prepare_readonly 复用同一物化引擎并省新根。建议保留共享实现，同时把“历史链长度与恢复时间”的策略从隐含无限增长变成显式保留政策；政策需要用户决定哪些完整点仍应保留。替换容器不会自动缩短恢复链。

Litestream 是成熟 SQLite 持续备份路线，其官方实现围绕数据库快照与 WAL 复制恢复。它可承担异机灾难恢复、保留和运维，仿真 step 与外部 artifact 的一致发布仍需映射到其恢复位置。当前应用依赖逻辑完整步骤和新 run 身份，贸然叠加 Litestream 会形成两套生命周期。建议保留为整库备份对照，先验“完整点精确选择和工件一致性”再决定是否替代目录协议。[Litestream](https://litestream.io/how-it-works/)。

独立复核补充：当前 v0.5 的 LTX TXID 可覆盖多个 SQLite 事务，实际可恢复位置取决于保留的完整 LTX 边界；压实与清理还会降低历史粒度。因此单独增加 step→TXID 映射不足以保留任意完整步骤。若选用它承担此职责，必须同时证明封存边界、保留策略和外部工件对应关系；否则限于整库灾备用途。

## 三、正文与压缩

自有实现最有机会缩减的部分在正文格式与压缩任务管理。应分别检验流式吞吐和随机定位，才能判断一个库替换后真正删除了哪些代码。

### 3.1 zstd 原生线程能够替换什么

python-zstandard 的 `ZstdCompressor(threads=N)` 在 C 层调度压缩任务，默认任务分段通常为 MiB 量级；输入小于分段时多线程可能仅使用一个工作线程。stream_writer/copy_stream 可以接受流式输入，context 及其线程、窗口和输出缓存仍有内存成本。原生多线程可以替换自写 future 队列的吞吐职责，固定 Python 队列字节上限需要改为实际原生内存计量。其 `multi_compress_to_buffer` 官方标为 experimental，且共享输出 buffer 可能延长整批驻留，不宜仅因现成 API 就优先采用。[多线程](https://python-zstandard.readthedocs.io/en/latest/multithreaded.html)、[压缩 API](https://python-zstandard.readthedocs.io/en/latest/compressor.html)。

对于按顺序生成、按顺序恢复的 changeset，原生 zstd 流是更直接的候选：Session 输出接压缩 writer，应用时接解压 reader，省去自行调度独立块。当前 complete 保存未压缩原生 changeset，采用后涉及 complete/restore 的格式标识和错误收束，不需要修改插件表。应先确认原生流适配的异常与短读合同，再比较总 CPU、压缩率、内存及恢复延迟。

Thread 的当前 64KiB 块还承担任意字节范围定位。把全部消息压成一个普通 zstd 帧，会改变这一合同；压缩输出的任意 chunk 边界也不等于可独立解码边界。逐个 64KiB 帧调用 threads=N 可能没有并行收益。建议暂保既有 Thread 布局，待公共正文格式评估后决定一起迁移；不要再扩展现有 ThreadPoolExecutor 框架。

### 3.2 Seekable 格式可替代自有块索引

zstd 官方 seekable format 由独立帧及尾部跳转表组成，标准解码器可顺序读，支持格式的 reader 才能直接定位帧。帧越小，范围读取放大越低，压缩率与索引开销则相反。[官方格式实现](https://raw.githubusercontent.com/facebook/zstd/dev/contrib/seekable_format/README.md)。

Python 绑定应明确区分：当前安装的 python-zstandard 文档提供丰富 streaming API，不能据此假定已有 seekable 文件 API；pyzstd 的 `SeekableZstdFile` 明确提供该能力，默认最大帧 1GiB，需要按访问模式配置。当前官方文档已说明其依赖 compression.zstd／backports.zstd 路线，项目 Python 3.12 必须实际核对可安装组合；本轮未安装或宣称部署通过。库读取 seek table 的驻留随帧数增长，也要计入“按需读取”的总内存，而非只计一个解压帧。[pyzstd 官方 API](https://pyzstd.readthedocs.io/en/stable/pyzstd.html#seekablezstdfile-class)。

实现成熟度还应看实际可复用代码：pyzstd 上游 `_seekable_zstdfile.py` 已实现文件读写、尾表加载、二分定位、append 与 seek，意味着可直接删除本项目的帧定位和跨帧解码实现，而非再写绑定。其 `_SeekTable` 读入时保存两组 int64 累计偏移，约 16×帧数 bytes，解析还临时读取整张约 8×帧数 bytes 的尾表；写入同样保留帧目录。按 64KiB 帧，约 1.879GB 逻辑流的累计偏移主体约 0.46MB，100GiB 约 25MiB，均未计解压上下文及其他对象。这是从上游实现推算，尚非本项目实测。因此该 binding 有现成完整功能，部署适配、固定版本、恢复异常及实际缓存行为仍需小型验收。[pyzstd seekable 源码](https://raw.githubusercontent.com/Rogdham/pyzstd/master/src/pyzstd/_seekable_zstdfile.py)。

Python 3.14 标准库也提供 `compression.zstd` 原生 workers、job size 和流接口；当前项目为 3.12，可优先维持现有 python-zstandard，单纯减少一个依赖不足以驱动解释器升级。标准库常规 ZstdFile 的 seek API 也不应被误认成官方 seekable 跳表支持。[Python 3.14 zstd](https://docs.python.org/3.14/library/compression.zstd.html)。

建议将 Dataset 自有 blocks 表、跨块拼接与 decoder cache 的替换列为优先候选：SQLite 保留 ordinal→未压缩 offset/length，正文使用标准 seekable zstd 工件。它会引入索引与正文两个不可变文件，现有 artifact 集合可以一起登记，完整发布仍由同一事务和完成描述符负责。若 Python 绑定的表内存、异常或跨平台支持不合适，保留当前 SQLite 容器更合理；无需自行重写官方 seekable 实现。成熟格式仍无法提供记录编号或授权，相关轻索引和分页身份需要应用保留。

## 四、替代存储

广泛比较的价值在于找到能直接接管现有责任的成熟系统，并准确计入迁移代价。数据库替换与文件格式替换应分开决定。

### 4.1 Arrow IPC、Parquet 和 DuckDB

Arrow IPC file 支持按 record batch 访问，stream 按顺序读取；内存映射有助于共享未压缩缓冲，压缩与转成 Python 对象仍会分配内存。适合固定类型数值、向量和批次交换；任意 JSON、无限精度 Python 整数及精确原文字节需显式编码约定，不能凭“支持 nested”就认为无损接收所有现接口。[Arrow IPC](https://arrow.apache.org/docs/python/ipc.html)。

Parquet 原生提供列块、页、压缩和元数据，DuckDB 可进行列投影及过滤下推。它们适合不可变事实表、结果表与分析数据集，能够让当前 Dataset 从“压缩 JSON 行容器”升级为现成分析格式。顺序要显式 ordinal，typed key 要显式类型列；不能依赖扫描的偶然返回顺序。巨型单个字符串的中间 64B 并非 Parquet 的通用读取合同，业务还需保留 Document／字节引用路径。[Parquet 格式](https://parquet.apache.org/docs/file-format/)、[PyArrow Parquet](https://arrow.apache.org/docs/python/parquet.html)、[DuckDB 官方扫描](https://www.duckdb.org/docs/current/data/parquet/overview)。

建议为有明确 schema 的正式批量消费者优先比较 Parquet+DuckDB，替换范围限 Datasets／Results 对应表型输入，不重写热状态关系库。维持任意 JSON 的消费者可继续使用行式正文。需测 record batch、row group 和结果批量的 RSS，完整 read_table 会物化全部内容；memory_map 也不会使解码免费。查询接口应调用原生 SQL／scanner，舍弃另造跨格式查询引擎的方向。

### 4.2 LMDB 与 RocksDB

LMDB 已提供单 writer、多 reader、内存映射和事务视图，Python 可借用 buffer；借用受事务生命周期约束，长读会阻止旧页回收。它适合简单有序 KV 和明确编码的记录，却要求把当前 SQL 查询、复合索引、约束及 Session 恢复合同重新实现或替换。建议保留为独立大 KV 机制对照，本轮舍弃“全面迁移以天然低内存”的推断；映射页 RSS、匿名内存和系统缓存需分开观察。[py-lmdb 官方绑定](https://lmdb.readthedocs.io/en/release/)。

RocksDB 原生 Checkpoint 能产生独立目录，同文件系统硬链接 SST，跨文件系统复制，元数据和必要日志由引擎管理。它确实覆盖当前希望拥有的低复制分叉能力，值得比自造不可变段管理更早研究。BlobDB 对大 value 提供键值分离与垃圾回收，减少大值参与 LSM 压实的开销；并不自动提供压缩大 value 内任意范围读取。[Checkpoint](https://github.com/facebook/rocksdb/wiki/Checkpoints)、[BlobDB](https://github.com/facebook/rocksdb/wiki/BlobDB)。

采用 RocksDB 的代价是插件 SQL schema、查询、外键、Session 都需换成 KV/列族及事务约定，还需验证实际 Python 绑定暴露的 checkpoint、事务、BlobDB 与生命周期能力。运行期 Snapshot 与落盘 Checkpoint 各有用途，跨重启恢复以持久工件合同为准。建议列为“若低复制热库分叉成为首要目标”的架构候选，当前证据不足以立即替换 SQLite。不要模仿其 SST/manifest/compaction 自行造一个简化版本。

## 五、编码与文件

编码器与文件系统已经采用原生资产，下一轮应收紧自有适配层的职责，避免把格式便利性扩大成任意对象或任意文件的低成本承诺。

### 5.1 JSON 与巨大单值

RapidJSON dump 的流式 sink 已被当前实现采用，原先 Python 逐原子遍历已移除。薄适配目前承担首个 sink 错误保存和停止后续副作用；已有试验证明原生遍历仍可能继续，取消并非即时。建议保留成熟编码器，核实上游错误传播修复后再删除适配；无需再次开发 JSON 引擎。[RapidJSON dump](https://python-rapidjson.readthedocs.io/en/latest/dump.html)。

orjson 提供快速原生编码，但返回整份 bytes、整数范围为 64 位、非有限浮点默认编码 null，这与现行大整数、拒绝非有限值及流式正文合同有差异。适合受限小消息的另行比较，当前通用路径建议舍弃直接替换。[orjson 官方实现](https://github.com/ijl/orjson)。SQLite JSONB 可减少 JSON SQL 函数的解析开销，其多数操作仍为 O(N)，它不等于压缩随机字节容器，也不解决 Python 任意大整数合同。[SQLite JSON](https://www.sqlite.org/json1.html)。

巨大单值无论采用哪种数据库，完整 get/read_messages 都有输出与解析内存下界。SQLite 单个 BLOB/行存在实现长度限制，原生 incremental blob API 适合定长 BLOB 分段 I/O；Session 对大更新行的捕获仍需另行计算。[SQLite limits](https://www.sqlite.org/limits.html)。热字段与不可变正文拆分应作为 schema 设计，而不是期待压缩器消除全值重写。

### 5.2 文件增量与跨文件系统

现有 Bashkit OverlayFs 已接管文件语义，建议保留。SQL 目录投影与 artifact 引用是持久化桥；复杂 copy-up、白化删除或路径解析应持续交给原生库，舍弃再造文件系统快照格式。对于巨文件追加，当前全文件 copy-up 仍需明确成本；可研究上游是否提供成熟分段文件或差分能力，已有 API 没有该能力时先陈述限制。[Bashkit 官方 API](https://docs.rs/bashkit/latest/bashkit/)。

不可变 artifact 的硬链接使源删除后目标仍独立存在，但要求所有参与者遵守不可变约定；写任一硬链接会改变同一 inode。跨文件系统复制具有真实 I/O 与空间成本，任何压缩格式均无法消除此物理条件。热 current 数据库不适合直接硬链接成独立可写分叉。建议保留现有同 FS 链接／EXDEV 复制，后续若采用 RocksDB Checkpoint，让数据库负责可写分叉的底层共享。

## 六、取舍

建议优先做四个有结束条件的选型判断，先减少自有责任，再考虑局部吞吐。每个判断都应使用既有固定样本，避免新一轮无限优化。

第一，保留 SQLite/APSW 热库、schema 和原生变更捕获；比较“完整步单事务”与现有短事务 Session 合同是否满足真实可见性需求。若保留后者，应用完成身份和工件发布依旧必要；恢复链政策明确化即可，不增加第二套日志。

第二，替换候选集中到正文：顺序 changeset 试原生 zstd streaming；随机冷正文试成熟 SeekableZstdFile 加轻记录索引；表型事实试 Parquet+DuckDB。当前 Dataset 自有块格式作为可测基线保留，确认候选部署、范围读取与总内存后再决定删除。原生多线程与 seekable 的组合必须实际验证，小帧独立性和多核吞吐存在参数冲突，官方能力清单不能替代性能证据。

第三，Thread 保留请求水位、原文、顺序和完成步关联，workspace 保留 OverlayFs 与规范引用；舍弃新增自有压缩任务框架、页管理、LSM、任意 SQL/JSON 通用索引等方向。原生库替换后仍需验证失败时不发布半个组件、读取预算、取消收束和源删除后的恢复。

第四，LMDB/RocksDB/Litestream 进入明确条件的候选库，而非同时装配成更多运行层。低复制分叉若成为主目标，RocksDB checkpoint 的原生价值值得承担一次完整架构对照；关系约束和插件 SQL 仍为主目标时，SQLite 更贴合已有消费者。选择标准是共同语义下总目录空间、CPU、RSS、恢复/分叉时间、维护代码和依赖部署成本。

本轮结论是：底层复用已经占较大部分，自有复杂度主要位于压缩记录容器、压缩排队和应用级完整点协议。前两者有明确成熟替代候选；后者需要先决定仿真的可见性和恢复语义，再判断能简化到什么程度。上述选择均停留在研究阶段，原验收推进与付费模型调用保持暂停。
