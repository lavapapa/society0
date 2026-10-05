# 存储机制第二轮优化

本文保留本轮 v2 编码器、临时事务耐久性和页大小试验的阶段证据。真实长键产物随后推动了 v3；最终实现、同一真实 World 的空间与时间权衡、顺序元数据和分页边界验收见 [v3 最终机制报告](storage-v3-real-keys-20260929.md)。本文中的 v2 参数和数字不作为最终 v3 发布结论。

本轮以 c2ad77b 为固定对照，保持完整记录正文、步骤提交边界、随机范围读取及恢复语义。原始负载包含 204882 条异质记录、138454364 字节 JSON，最大值约 2.79MiB；合成负载的 World 根发布另覆盖 1899238802 字节。时间单位为秒，内存和文件大小均记录原始字节。

## 一、依据

cProfile 记录了约 1.25 亿次 Python 调用，其中逐片 JSON 生成占主要时间；4097990 次 json.dumps、约 1450 万次 bytearray.extend 和 409904 次 SQLite execute 是可避免的重复工作。原 gzip 压缩约占 1.3 秒。详见 storage-c2ad77b-profile.txt；剖析器自身有开销，剖析耗时不与普通计时混用。

实现使用 64KiB 保守预估，通过有界遍历后让 C JSON 编码器一次处理小记录；大字符串维持 8192 字符分块。SQL 每批最多 512 行，并同时限制到 64KiB 的保守元数据预算；单条超预算直接写出。路径前缀缓存同时受 256 项及 64KiB 限制。SQLite 临时排序放文件，页缓存 2MiB，记录路径索引在发布前统一构建。每个路径的总数附于首条记录，避免重复路径表与逐条 UPSERT；压缩块和精确范围接口不变。格式明确为 sqlite_records_v2。

多步 epoch 的未发布记录省略查询索引，最终合并复制压缩块后统一建索引。完整标记仍在 SQLite 关闭、文件同步与原子发布之后写入。暂存文件不构成恢复边界。

## 二、选择

1024 字节页使 40 条简单 delta 从 4096 页下的 28672 字节降至 7168 字节；512 页为 6656 字节，但大负载增加文件和写入时间，小增量采用 1024 页。大根与至少 4096 条记录采用 4096 页：40/256/1024/4096 条异质记录的页大小对照保存在 storage-page-threshold.json，条数达到 4096 后，小页文件节省已仅约 2.4%，大页随机读取更快。选择只用 root 身份与现成条数，不预扫 generator；merge 读取各源 totals 后按总条数选页，若首源页不同，直接新建目标并复制所有源一次，不做 VACUUM 或额外全量副本。异质负载 gzip 3 与 gzip 6 分别为 47.82MB/2.23秒和46.03MB/3.08秒，3级多约3.9%磁盘换约27%写入时间下降。Ceph 同数据对应12.46秒和14.83秒；1级12.14秒/47.92MB，优势很小且文件更大，采用3级。原 c2ad77b 在 Ceph 为22.04秒/52.86MB。

本地 2048、20488、204882 条负载额外峰值 RSS 为7.54、11.63、16.35MB；2048820 条流式输入在最终4096页版本为24.90MB。后一项专门检验 SQL 索引与编码工作区随条数增长的内存，不代替 World 根路径测试。合成负载的 World 根路径额外7.49MB，6.19秒、21.82MB文件；明确禁止调用整 World 快照。测量支持本组负载暂存开销低于32MiB，单条任意大路径元数据仍可能需要与该路径大小相应的内存，不能据此宣称任意输入恒定32MiB。

## 三、验证

新增测试先失败：小记录编码调用12次而非1次；4096记录SQL调用8200次而非小于100；200条长路径单批元数据600890字节而非不超过65536；pending参数不存在。实现后这些断言通过。小记录、大字符串、Unicode转义、非finite和标量键语义、共享块跨边界范围读取继续验收。

命令统一在本工作树使用 PYTHONPATH=src，以及基础仓库 .venv/bin/python。直接组命令为 `python -m pytest -o addopts='' -q tests/primary/test_checkpoint_records.py tests/primary/test_storage_review.py tests/primary/test_incremental_checkpoint_v4.py tests/primary/test_persistence_manager_v4.py`，首轮43通过记录在storage-optimization-tests.txt。后续独立review增加用例后重新复验。

20步纯环境与含本地确定性Agent对照保存于stage-comparison-optimization.json。初始、最终及恢复状态、Thread全文、DummyMemory正文和provider实际消息输入均等价；该对照不包含真实模型等待时间。5千/5万history根发布分别从0.120/1.158秒降至0.040/0.357秒。多步5×2万条暂存加合并从2.429秒降至0.770秒，暂存文件9.35MB降至4.18MB。

Ceph只使用本任务临时目录 simulation:/mnt/data/l20/qin/runtime-observation-storage-20260929，未操作现有runs或服务。远端使用system python3标准库，Linux ru_maxrss已换算为字节；同期独立真实测试可能有少量IO，结果用于相同机器同输入机制比较，不能当作隔离部署吞吐保证。原始结果保留，合成数据在TemporaryDirectory退出后删除。

## 四、耐久边界

Ceph 多步合并的细分计时揭示初版 merge 从4.20秒增至7.94秒；虽总耗时下降，重复耐久写入仍值得消除。最终实现将私有临时 SQLite 的 journal_mode 与 synchronous 设为 OFF。该文件在构建期间没有读者，任何失败后均不发布 marker；成功路径继续在关闭数据库后 fsync 文件、原子 rename、fsync 目录，再由检查点层发布完整 marker。已发布 source 始终只读。此改动不涉及在线观察索引、Thread 或 Chroma 的事务策略。

新增耐久测试先因仍使用 DELETE/FULL 失败，改后通过；另外注入磁盘满、索引创建失败、最终文件 fsync 失败，并在独立子进程索引构建期间 os._exit(91)，都保持旧完整 root 可恢复、新 step 无完整 marker。直接相关42项通过。threads 非作者独立复验11项通过，覆盖严格 close→file fsync→rename→dir fsync 顺序、目录 fsync 失败无新 marker、1024→4096/4096→1024 混合源合并、完整正文与精确 total、源文件字节不变。

本轮交叉审查由存储作者独立检查 query 新实现。发现 current 页在 status 后并发索引提交时可能把新值标成旧 checkpoint，新增红测明确得到 new 而预期 old。query 作者通过整页 WAL 读事务修复，存储作者复验20项通过；另覆盖同 epoch 重复 set、typed int/string 路径删除重建、总数和旧版本 prepared 页一致性。存储实现由 threads 独立审查，另加 C/流式编码逐字节一致、大转义值范围和混合 pending/indexed 合并测试。

最终自适应版本 Ceph 异质负载为6.447秒/47575040字节，旧 c2ad77b 为21.580秒/52858880字节；100次随机读取中位4.134ms，旧4.873ms。5×2万条epoch总耗时4.124秒，旧32.066秒。单次尾部50字节读取受到毫秒级波动影响，读取判断以100次分布为主，原始值全部保留。合成负载的World根最终为6.196秒/21600339字节/额外7143424字节RSS。

## 五、身份解析

近期运行产物的 root 分解测量进一步定位到历史实现的隐性全量恢复：resolve 即使仅请求 checkpoint 身份也调用 restore，configure_existing 又分别解析 root 与 latest，discard 会再次重建状态。新增三项测试先失败，分别复现 generator 根因可选 metadata 缺省而被当成增量、纯身份解析调用 restore、含恢复结果的 resolver 重读两次 manifest chain。

最终根身份由 publish_root 入口明确传入空 metadata；generator 无 len、预扫或物化。resolve(False) 与 fork 共用组件校验：流式校验现有组件摘要、state 链、Thread 恢复资格、base 来源身份和 SQLite 实际记录数，正文留给真正恢复路径。resolve(True) 复用已读取的 chain 构建一次状态。configure_existing 解析最新完整 checkpoint 一次；discard 使用已验证或已发布的 committed step 和 epoch 集合，测试禁止其调用 resolve，保持已提交内存可见性并清除未发布暂存。

1.899GB 合成World存量上的身份解析约9ms，RSS未超过此前根写入形成的高水位；这个差值不表示临时内存为零，独立进程身份解析另行测量；详细结果见storage-root-resolver-final.json。threads 负责组件损坏、最新回退、base/Thread 与冷启动路径的独立复验。此修复要求最终真实跨进程恢复在当前源码重跑，先前真实测试结果保持其原代码身份。

独立新进程仅做合成检查点身份解析：起始RSS24002560字节、峰值42647552字节，额外18644992字节，耗时69.9ms；包含首次Thread校验模块加载。已加载模块的原进程existing configure为9.89ms，discard为15µs。最终存储与query联合86项通过；独立review包含坏组件最新回退、显式fork拒绝、base身份替换拒绝和禁止读正文仍能身份解析。

最终Ceph合成root writer同数据原文1896575336字节，从29.731秒/25608192字节降至18.182秒/21581824字节，额外峰值RSS7680000字节。此项直接测不可变records writer；本地1899238802字节的World→manager路径另列，近期真实运行产物由spec独立测量。远端全部合成数据库已自动清理，保留小结果及可重复执行脚本。最终最新交叉审查合并重跑37项通过4.52秒。
