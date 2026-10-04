# Chroma 派生索引驻留测量

新版规范 SQL、不可变正文和按页读取已经把领域状态的工作集与全部冷正文分开。向量候选索引仍有独立的容量成本。本次使用当前安装的 Chroma 1.5.9 持久化客户端测量这一边界，原始结果在 `chroma-residency-20261004.json`，执行脚本为 `benchmarks/core_next_chroma_residency.py`。

## 一、测量

每个配置使用独立 Python 3.12.12 进程和临时目录，在本机 macOS ARM64 上建立一个 collection；1024 维 float32 随机向量固定种子，256 条一批，原始输入批次为 1 MiB，SQL 元数据含主体标识。删除批次数组并垃圾收集后用系统 `ps` 读取进程 RSS。测量不调用模型或嵌入服务，也没有保留全部输入数组。实际 query 的首项是所查询的原向量，取回向量逐值相等。

1000 条时客户端初始化 RSS 为 94.54 MB，写入后 125.53 MB，查询后 126.48 MB；磁盘文件总长度 9.30 MB，构建 0.131 秒，查询 1.44 毫秒。10000 条时初始化 94.90 MB，写入后 171.51 MB，查询后 172.33 MB；磁盘 46.39 MB，构建 1.752 秒，查询 6.43 毫秒。两个独立进程的写后 RSS 相差约 45.97 MB，新增 9000 条原始 float32 向量为 36.864 MB。此差额包含原生索引、元数据与分配器驻留，不能全部归因为 HNSW 节点。

上述时间包含 Chroma 原生多线程，10000 条进程 CPU 为 8.58 秒、墙钟为 1.82 秒。RSS 是指定阶段瞬时读取；JSON 中 `peak_rss_bytes` 是取回向量后、最后一次 RSS 读取之前采样的进程高水位。操作系统缓存未清空。单次 1k/10k 结果足以证明额外增长，无法推导百万条容量上界或长期查询延迟分布。

## 二、配置

本机实际后端是 `chromadb.api.rust.RustBindingsAPI`。给 10000 条同源数据设置 `chroma_segment_cache_policy='LRU'` 与 `chroma_memory_limit_bytes=1048576` 后，写后 RSS 仍为 173.33 MB，磁盘仍为 46.39 MB。默认与此设置的 native HNSW cache 都为 209715 个条目。

这与安装版本源码一致：`RustBindingsAPI.__init__` 按文件句柄上限除以 5 设置 `hnsw_cache_size`，`start()` 将该条目容量传给 Rust bindings，未传递上述 Python Settings 的字节限额和策略字段。该结论限于本次安装版本与默认 Rust 后端，不能把旧 Python segment manager 的 LRU 示例当成当前字节预算。可核对 [Chroma 1.5.9 原生客户端源码](https://github.com/chroma-core/chroma/blob/1.5.9/chromadb/api/rust.py)。官方单机容量说明也将内存中的向量搜索结构作为主要约束，见 [单机性能文档](https://docs.trychroma.com/guides/performance/single-node)。

目前 Memory 通过注入客户端使用一个运行级 collection，主动主体数减少不会自动移除其他主体的当前有效向量。历史时点查询还会为对应主体建立临时 collection，查询结束删除；索引建立期间的原生峰值与删除后分配器驻留尚未由本次试验量化。此前领域冷读约 22 MB 的进程结果不包含 Memory、Chroma 或模型 SDK。

## 三、边界

当前 Memory 对索引依赖集中在 `sync_index`、`recall` 与 `_recall_history`：创建和删除 collection、读取和修改水位 metadata、按明确 id upsert/delete、按 actor 与 visible_step 过滤并返回 id/距离。正文、原向量及历史版本仍在规范 SQL。后续替换候选引擎可以沿这个边界实现薄适配，但必须保持 L2 距离口径、当前和历史版本选择、候选数量、过滤行为、排序与重排结果，不能把近似检索器的名称相同视为行为等价。

下一步先比较成熟磁盘向量索引或已有受控内存服务的实际召回、驻留与 I/O 成本，也可评估按主体工作集装配成熟索引。把服务迁到另一进程会改变归属和隔离，系统总内存仍需合计；本轮没有实现新后端，也没有缩减记忆原文或候选语义。

本次结果把向量索引明确列为独立规模边界。现有持久化优化继续成立，同时需要把真实运行的向量数量、维度和历史查询峰值纳入部署预算，不能用领域状态读取的低驻留代替完整认知运行的成本。
