# 存储与压缩首选方案

本稿于 2026-10-04 收敛下一版设计，产品参考提交为 `0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0`。七个相关模块已逐字核对，结果见 [源码身份](storage-selection-20261004/source-identity.json)。本轮新增隔离小试验，未修改产品、项目锁文件或执行模型调用；设计停在选型和后续实施条件。

**首选是 SQLite/APSW 热状态及 Session 完整点、标准 seekable zstd 冷批次、同库 zstd 小消息、RapidJSON 流编码与 Chroma 默认记忆检索。** 压缩的 Python future 队列拟删除。sqlite-vec 精确检索保留为唯一显式向量后备；Parquet/DuckDB、LMDB、RocksDB 和 Litestream 均不进入首版基础依赖。

## 一、确定选择

这套选择保留已有语义，把可被成熟库完整接管的责任移出本项目。统一的是编码器、压缩算法、引用和读取合同，物理容器依数据事务边界确定。

### 1.1 热状态和完整点

热状态继续用 SQLite/APSW，普通插件声明有显式非空主键的规范表，同一 writer 以短事务同步事实与当前投影。冷文件必须先关闭并耐久，再在规范事务登记引用。完整步骤继续使用原生 Session 流和一份原子发布的完成描述符；描述符发布前的 live 状态可被观察，恢复选择 root 加已经发布的完整链。

保留这一边界的依据是实际运行同时需要阶段内可见性和跨插件完整步骤恢复。把一整步扩大为长 SQLite 事务会改变即时 Thread/诊断可见性；本轮不采用。Session 流式 API、backup 和 apply 已在现项目及本轮小试验中跑通，数据库页缓存、日志、回滚和索引继续由成熟实现负责。[SQLite Session](https://www.sqlite.org/sessionintro.html)、[APSW Session](https://rogerbinns.github.io/apsw/session.html)。

完成协议收敛为“准备不可变文件—规范事务登记—封存 changeset—发布完整描述符”。恢复和只读准备共享物化路径；同文件系统用硬链接复用已封存文件，跨文件系统复制；读观察服务仍使用短事务。保留目录 fsync、取消收束和孤儿回收合同。该顺序没有给两个文件系统提供分布式事务，工件准备失败或规范事务回滚产生的未引用文件由既有离线回收处理。

### 1.2 冷批次和在线消息

冷 Dataset 首选 `pyzstd.SeekableZstdFile`，最大未压缩帧为 64KiB，默认串行原生压缩。一个批次使用一个标准正文文件和一个 SQLite 记录目录；目录保存 ordinal、原文字节起点与长度。库接管帧目录、seek、跨帧读取与缓冲，删除现有 blocks 表以及本项目的帧拼接和解压缓存实现。两个不可变文件作为同一批次的依赖登记，避免新增发布引擎。

Thread、Memory、ResourceCalls 的在线消息继续把元数据和正文放在同一规范 SQLite 事务。小消息逐条外置会增加文件数、同步次数和失败窗口，本轮不采用。正文仍为独立 64KiB 标准 zstd 帧，由成熟原生串行 compressor 直接写规范 SQL；极小的 seq/chunk SQL 索引保留。这样能够删除自有线程池，又保留即时 tail 和巨消息范围读取。将来若要统一在线消息与冷批次的物理容器，需要先证明整步封存如何满足即时读和崩溃诊断，不列入首版任务。

## 二、压缩证据

新试验专门比较格式与任务管理，完整过程在 [probe.py](storage-selection-20261004/probe.py)，命令、版本及结果位于同目录。固定种子生成 12000 条异质 JSON，总原文 20,791,529 B，其中一条约 2.8MB；各路线共用同一 SQLite 记录目录。每路线为独立进程，临时大文件自动清理，正文全流与 502 次随机范围逐字对照。

### 2.1 Python 3.12 的实际可用绑定

隔离环境实际安装成功：pyzstd 0.19.1、backports-zstd 1.7.0、zstandard 0.25.0、APSW 3.53.4.0、RapidJSON 1.25。首版压缩绑定选择 **pyzstd 加其 backports-zstd 依赖**：前者提供标准 seekable 文件，后者提供标准库风格的压缩流及独立帧。项目维持 Python 3.12，未来升级解释器时再考虑标准库替代 backport。当前 python-zstandard 作为对照已验证，正式迁移后可从该存储路径移除，避免同时维护两套基础绑定。

上游 SeekableZstdFile 已实现尾表解析、二分定位、读写和 append。本次实际全流及随机读通过，具备成为首选的功能依据；Linux wheel 与项目集成生命周期仍需后续验收。它会装载帧目录，约为两组 int64 累计偏移，并在解析时临时读取尾表，因此元数据驻留随帧数增长。64KiB 帧下约 1.879GB 原文的目录主体约 0.46MB，100GiB 约 25MiB；这是按实现结构推算，未冒称固定总内存。[pyzstd API](https://pyzstd.readthedocs.io/en/stable/pyzstd.html#seekablezstdfile-class)、[实际实现](https://raw.githubusercontent.com/Rogdham/pyzstd/master/src/pyzstd/_seekable_zstdfile.py)。

### 2.2 冷数据选择 64KiB 帧

前三次样本中，现 SQLite 块布局含记录索引为 13,783,040 B，seekable 64KiB 加相同索引为 13,493,881 B，约少 2.1%；502 次随机范围读取分别约 28–30ms 与 29–36ms，进程写阶段绝对峰值约 33MB 与 31MB。新首选价值主要是删除自有块逻辑，空间和随机读性能保持相近。

1MiB 帧将空间降至约 13.416MB，但随机范围读取增至约 210–234ms；因此首版选 64KiB。独立帧配置四个原生 worker 在前三次未显示稳定收益，且增大部分峰值。连续单帧四 worker 流为约 23–30ms、峰值约 67MB，顺序写更快，但没有随机读取合同。读计数另显示 seekable reader 可能预读超出请求帧，不能把帧大小直接当物理读取字节上限。所有页缓存均保留，计时包含文件写入和文件 fsync，范围测量含与原文对照的读取，未称物理冷盘纯查询延迟。

最终第四次将边界查询明确落在全局 64KiB 帧边界两侧，全流与全部 502 次范围比较仍相等。SQLite 块布局与 seekable 串行的写入为 52.36ms、45.24ms，范围读取为 30.17ms、29.97ms；四 worker 本次为 36.38ms，反映小样调度波动，结合前几轮仍不足以将其设为默认。详细记录见 [最终范围复验](storage-selection-20261004/compression-r4.json)。

标准多线程压缩与 seekable 随机读是两个能力。小独立帧经常小于原生并行任务粒度；本轮不为多核数字增加跨帧 Python 队列。大规模顺序 changeset 可显式启用少量原生 workers，默认仍串行，以控制内存和小文件开销。[原生多线程限制](https://python-zstandard.readthedocs.io/en/latest/multithreaded.html)、[seekable 格式](https://raw.githubusercontent.com/facebook/zstd/dev/contrib/seekable_format/README.md)。

### 2.3 Thread 队列可以删除

另以一份原始 10MiB 合成正文连续写四条消息，采用现行 RapidJSON，包含实际文件写入和同步；计时外逐条完整 JSON 值相等。现产品 zlib 四线程池约 220–225ms 墙钟、803–822ms CPU、约 78MB 峰值，压缩正文总 32.342MB。串行 backports-zstd 独立 64KiB 帧两次为 98–128ms、72–112ms CPU、约 74MB 峰值，总 31.490MB。绑定自带原生压缩已足以支持删除 future 队列、预读阈值和工作池关闭代码。

第四次相同输入的现 zlib 池与原生串行 zstd 分别为 200.71ms、72.81ms 墙钟，798.65ms、71.08ms CPU，绝对峰值 77.63MB、74.22MB，四条原文仍全部相等。前述多次结果共同支持方向选择，未做统计显著性推断。

这是格式适配试验，最终 sink 是顺序文件而非正式 Thread SQL；因此支持“选择串行 zstd 实施”，尚不宣称端到端模型循环同比加速。sink 首错保持、SQL 规范线程写入、异常回滚和取消仍由既有产品集成测试验证。输入原对象、UTF8 缓存及完整 read_messages 的内存下界继续存在，64KiB 约束针对流式输出。

## 三、恢复与格式

成熟压缩器不会自动创建跨插件完成语义。首版重点是给已有发布协议换更简单的正文工具，并避免再叠一个运行系统。

### 3.1 原生 changeset 可直接接压缩流

[session_stream.py](storage-selection-20261004/session_stream.py) 使用 APSW 创建根、两个短事务插入 200 行，再将 `changeset_stream` 直接写入 backports.zstd.ZstdFile；恢复时以其 read 方法直接交给 `Changeset.apply`。200 行全部相等，完整点之后的额外 current 写入被排除。该样本捕获内存 13,872 B，压缩 changeset 1,479 B，压缩及恢复约 6.34ms。它验证原生 API 可以直接衔接，未重写或重新证明磁盘故障下的完整发布协议。

大 UPDATE 行仍使 Session 保存旧行，采用 patchset 或流式压缩也不消除捕获内存。首版仍选 changeset，保留其原始值冲突判定；冷热字段拆分是插件 schema 的设计义务。Session 仅跟踪规范连接的受支持表，冻结 schema 与明确主键要求保留。虚表索引继续作为可重建派生资源。

### 3.2 首版不引入其他数据库与归档系统

Parquet/DuckDB 对有 schema 的批量分析很有价值，但当前首版的关键消费者要求任意 JSON 原文、巨值范围、Thread 顺序和小事务。加入其依赖不会自动删除这些职责，因此首版不引入；未来表型结果分析由明确插件采用。[Arrow IPC](https://arrow.apache.org/docs/python/ipc.html)、[DuckDB Parquet](https://www.duckdb.org/docs/current/data/parquet/overview)。

LMDB 与 RocksDB 会要求迁移当前 SQL 约束和插件查询。RocksDB Checkpoint 的成熟共享 SST 方案值得在热库廉价分叉成为主需求时重新评估，本轮不把整个 KV 系统引入为一个库特性服务。[RocksDB Checkpoint](https://github.com/facebook/rocksdb/wiki/Checkpoints)。

Litestream 归为独立灾备工具。官方 v0.5 的 LTX/TXID 表示可能覆盖多个 SQLite 事务的批次，恢复按仍保留的整 LTX 边界进行，压实与清理可能永久移除细粒度位置。step→TXID 映射本身不足以保证精确完整步骤，外部文件也需共同保留；LTX 内置页校验字段与项目现行约束还需独立决策。因此首版完整点继续使用原生 Session，而不默认接入 Litestream。[官方恢复粒度](https://litestream.io/how-it-works/)。

## 四、向量与查询

这里的首选首先受主体认知合同约束。底层内存更小并不意味着可以静默替换候选集合。

### 4.1 Chroma 默认，精确检索显式后备

首版继续 Chroma 默认检索，保留现行候选数量、距离、重排和记忆历史语义。sqlite-vec 普通表加 actor 索引、scalar exact 是唯一后备，必须由运行配置显式选择并写入研究合同；不采用 vec0 默认稀疏分区，以避免既有小主体试验中的预分配放大。

既有 [向量报告](vector-backends-20261004.md) 用 10 个主体、10000 条 1024 维 float32 向量测得 SQLite scalar 约 41MB、Chroma 约 170MB 查询后 RSS。本轮补单主体 10000 条，20 次 top20 查询分别为 219ms 和 207ms，查询后 RSS 42.2MB 和 178.4MB；两者索引磁盘约 46.2MB 和 46.0MB。单主体增大时 scalar 的扫描代价已可见，后备策略须承认 O(该主体有效向量数×维度) 的 CPU/I/O。

更重要的是，同一合成样本上 Chroma 相对 float64 精确参考的候选重合平均 78%、最低 45%，scalar 为 100%；这些差异不自动说明任何一方主体效果更好。现有 importance/time 重排的输入会变化，M07 原语义因此无法据此宣称等价。向量输入本身为 float32；规范 float64 原向量转检索 float32 的量化合同仍待独立验证。没有重新 embedding，也没有更换 Memory 产品。

### 4.2 查询保留数据库与应用边界

首选查询基础是 SQLite 注册投影、主体授权过滤、明确排序索引和短快照；完整巨文走 blob/range 路径。游标绑定 run、相关数据版本和权限依赖，total 与结果来自同一查询快照。任意历史分析可以显式扫描，当前业务仍使用活动索引。

查询最终选择现有 APSW 注册投影、小型白名单查询语法与 SQL keyset，首版不引入 SQLAlchemy/sqlakeyset 的第二连接或 ORM 层。能由通用库代替的主要是几段排序拼装，授权、相关版本、挂载目录、scope、巨文和字节预算仍属于本项目，新增桥接会增加维护责任。需要全文检索时直接使用 SQLite FTS5 排名并按派生索引合同管理，避免开发通用查询引擎。当前 SQLInformation 的完整 355 行不应全部计作可删除分页代码。

## 五、维护预算

代码规模采用可复现 [count_code.py](storage-selection-20261004/count_code.py)：排除空行、注释与 docstring，保留多行 SQL 字符串，按物理源码行计。该口径衡量项目自有实现，不把依赖内部代码隐去后称复杂度消失。

当前 storage 680 行、datasets 160 行、_json_chunks 89 行、threads 308 行、workspace 84 行，合计 1321 行。另 SQLInformation 355 行、Memory 578 行，七模块共 2254 行；原生文件系统桥、测试、文档和其他插件不在这个合计内。

按首选实施，存储五模块预算约 **1200–1400 行**，属于设计估算。编码／压缩适配预期由 89 行降至约 30–45 行，删去自有 futures、预读和线程排空；Dataset 换标准文件后约 120–160 行，帧索引删除但双工件登记与记录目录仍有成本；storage 大部分完整点、身份、生命周期和 schema 代码保留，新增标准压缩流接线预计少于 30 行；Thread 与 workspace 的领域证据／文件桥保留。最终总量可能接近当前，维护收益首先来自移除高风险的任务管理与容器代码。

Chroma 默认路径在本轮不因选择后备而扩成模拟 collection API 的兼容层。后续若实现 scalar 后备，应使用小型候选检索接口直接返回 immutable version id 与距离，限定新增约 60–100 行适配；Memory 正文、版本与最终评分共用。此区间是实施预算，达不到时应重新审视范围，不能用压缩排版满足指标。

## 六、实施界限

下一版 PRD 可以冻结本稿首选，但本轮停在设计。最小后续实施次序是先迁移标准 zstd 在线正文，删除自有队列；再用 seekable 文件替换冷批次块表；最后给 changeset 接原生压缩流。三个步骤分别保留完整值、范围读取、恢复和取消失败验收，避免一次引入多种无法归因的格式变化。

已确认的小样证据覆盖 Python 3.12 安装、标准格式完整读、500 次随机加巨值尾部与边界范围、四条 10MiB 原文、原生 Session 流，以及单主体向量候选。现有同源 1448471 条冷根约 226.54MB、全值相等的结果继续作为正式集成基线；新 seekable 方案尚未重跑该大根，20.8MB 合成比例不用于推算全根压缩率。后续必要门槛包括 Linux wheel、标准 seek 表开销、每请求打开文件代价、损坏／关闭错误、SQL 和完整点故障窗口、跨文件系统复制及正式消费者延迟。

全部新证据都在 [隔离试验目录](storage-selection-20261004/)，精确命令见 [commands.txt](storage-selection-20261004/commands.txt)。临时输入和数据库由 TemporaryDirectory 清理，正式环境与项目锁文件保持不变。当前设计已经给出可执行首选与唯一向量后备；原验收和付费模型任务继续暂停，下一步由新的 PRD 明确授权实施范围。
