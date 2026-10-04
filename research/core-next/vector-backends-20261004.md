# SQLite 原生距离与 Chroma 候选检索

本次小试验针对当前记忆候选索引的内存增长，使用成熟的 sqlite-vec 0.1.9 扩展比较普通 SQLite 表和 vec0 分区表。产品 Memory 未修改。安装位于独立临时环境 `/tmp/society0-vector-study-20261004`，原始结果为 `vector-backends-20261004.json`，脚本为 `benchmarks/core_next_vector_backends.py`。

## 一、口径

三条路线在独立进程中使用相同固定种子的 float32 向量，维度 1024，未归一化的均匀随机分量，10 个主体；每批 256 条，单批原始向量为 1 MiB。20 个查询均限定 actor=3，返回 top20，对应 Memory 默认 top_k=10 的两倍候选。Chroma 使用当前安装的 1.5.9 PersistentClient、L2 HNSW 默认配置。普通表使用 actor 索引筛选后由 sqlite-vec 原生 scalar 计算距离；vec0 使用原生 actor PARTITION KEY 与 KNN MATCH。SQLite cache_size 为 2 MiB，关闭 mmap。

扩展实际返回欧氏距离，Chroma L2 返回平方欧氏距离。脚本在取回 scalar/vec0 结果后平方，另以流式 float64 运算生成精确 top20 参考，不在被计时的索引与查询阶段保存完整输入数组。向量生成、入库、索引与查询均计时，验证单独计时。相关功能定义来自 [sqlite-vec 官方 reference](https://github.com/asg017/sqlite-vec/blob/v0.1.9/reference.yaml) 与 [原生 vec0 结构说明](https://github.com/asg017/sqlite-vec/blob/main/ARCHITECTURE.md)。这是一款 0.1 系列扩展，能加载和跑通本机验收尚不等同于本项目完成长期部署验证。

## 二、结果

1000 条时，Chroma 查询后 RSS 125.52 MB、磁盘 9.25 MB，构建 0.111 秒，20 次查询共 12.82 毫秒。普通 SQLite 表对应 42.21 MB、4.63 MB、7.48 毫秒和 2.65 毫秒。vec0 对应 46.55 MB、42.13 MB、54.51 毫秒和 9.07 毫秒。默认 vec0 按主体分区时每个分区预分配块，稀疏小主体在此样本上产生显著空间浪费。

10000 条时，Chroma 为 170.21 MB、46.01 MB，构建 1.691 秒，20 次查询 92.73 毫秒。普通表为 41.04 MB、46.20 MB，构建 83.68 毫秒，查询 32.63 毫秒。vec0 为 46.50 MB、42.25 MB，构建 2.335 秒，查询 10.53 毫秒。普通表筛选的是该主体的 1000 条向量，扫描工作量仍随这个主体的有效记忆数增长；它将容量成本转为查询 CPU 与 I/O，并未得到与历史无关的检索复杂度。

1000 条三条路线均与精确 top20 一致。10000 条 Chroma 的平均候选重合率为 97.75%，最低 90%；两条 SQLite 路线均为 100%。共同候选的平方距离与 float64 参考最大误差约 3.21e-5。该随机样本用于容量和候选算法对照，未代表真实 embedding 的距离分布，也没有运行 Memory 最终认知评分。当前 Memory 的后续重要性与时间衰减重排可能受候选差异影响，因此精确检索应作为显式选择并记录在运行合同，不能静默宣称与 Chroma 完全等价。首轮标量表达式重复计算了距离，原始首轮文件另存 `vector-backends-first-20261004.json`；最终结果已改为计算一次、取回后平方。

## 三、接入

当前证据支持继续验证“普通 SQLite 表＋actor 索引＋原生精确距离”的可选路线。最小设计把候选选择收敛成 `current_candidates(actor, query_vector, k, visible_step)`、`historical_candidates(actor, query_vector, k, visible_step)` 与 `close()`，返回带不可变版本身份的 id/距离。前两者分别查询当前投影和指定可见时点的版本 SQL，第三个关闭本服务拥有的短事务读取资源。正文与评分继续由现有 Memory 完成，省去模拟 collection、metadata watermark、临时历史 collection 的接口层。

实现前仍有一个明确的数据成本：规范向量当前保存 float64，sqlite-vec 的 native float 向量采用 float32。本次实验原始输入就是 float32，尚未证明任意 float64 量化后的检索等价。实际可选实现应保留规范 float64，同时在同一规范写事务写入每条当前/历史版本的 float32 检索列，更新和删除保持一致。按每维 4 字节计算，这会增加可重建检索表示和 changeset 成本；需与现有 Chroma 已持有的另一份向量及索引总成本比较，不能把它记作免费转换。

读取侧需要在明确持有的只读 SQLite 连接装载扩展并保持一条候选 SQL 的快照。现 StageReader 没有公开扩展装载入口，产品接入应先决定最小连接初始化合同；应避免插件绕过规范读取生命周期。查询向量仍在网络等待结束后进入短读事务；历史候选直接携版本 id，后续正文按这个 id 获取。当前与历史索引按 actor、可见区间构造，查询性能必须在固定主体有效量和版本历史增长两类负载中分别验证。

本轮较小数据已经显示值得继续，优先普通表路线能保持简单的行存储和主体索引。下一阶段的必要验收是 float64→float32 的排序差异、单主体更大工作集的查询延迟、版本查询与恢复，以及包含规范库、派生列和增量的总占盘；当前结果不授予替换默认后端的结论。
