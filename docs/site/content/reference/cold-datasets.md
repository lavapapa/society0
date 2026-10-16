# 不可变数据集 Datasets

`from society0.kernel.datasets import Datasets, dataset_plugin` 用于批量导入稳定正文，并以轻引用连接热状态：正文放进独立的 SQLite 工件，热库只留登记与业务引用。数据集使用基础依赖，谁能看到哪些行由持有引用的领域机制与 Information 提供者负责，Datasets 本身不做授权。

## 导入一个批次并封存工件

一个导入批次生成一个不可变 SQLite 工件，热库保存登记与业务引用。

<!-- api: society0.kernel.datasets.Datasets -->
<!-- api: society0.kernel.datasets.dataset_plugin -->
<!-- api: society0.kernel.datasets.Datasets.import_rows -->
<!-- api: society0.kernel.datasets.Datasets.describe -->

```python
reference = datasets.import_rows('catalog', rows, attach=attach_catalog)
```

rows 为单次消费的 JSON 行迭代器；attach_catalog 是调用者提供的同步 `(writer, reference)` 回调，把引用写进领域热状态。数据集登记、include_artifact 与 attach 在同一规范事务提交；attach 返回 awaitable 抛 `TypeError`。导入返回含 kind、id、artifact 的引用，调用者应原样保存。

消费和编码成功后封存尾块、关闭工件数据库，再同步文件并发布路径。导入或封存失败传播原异常，业务引用不发布；封存成功而登记事务失败时留下可离线清理的孤儿。完整步骤发布后该引用才属于可恢复事实。

**sealed 数据集的正文工件必须一直保持不可变。** 新资料应新建批次并更新领域引用；同文件系统恢复可能硬链接共享原工件，原地修改会影响多个目录。Datasets 借用规范 store，工件归该运行的完整步骤生命周期管理。

## 分页读取与原文续读

分页信封保留总体规模，大值可按字节继续读取。

<!-- api: society0.kernel.datasets.Datasets.page -->
<!-- api: society0.kernel.datasets.Datasets.read_payload -->
<!-- api: society0.kernel.datasets.Datasets.get -->

page 返回 items、精确 total、next_cursor。页项带 ordinal、raw_bytes 以及 value 或 payload_ref；业务 JSON 完整放在 value 内。limit 为正整数，max_bytes 至少 512，最终预算包含分页外壳。页放不下首项或引用时报 `ValueError`，应扩大预算。游标绑定运行和数据集，恢复分支从第一页开始。

`read_payload(reference, ordinal, offset=..., size=...)` 返回 bytes、total_bytes、next_offset 组成的字典。偏移按实际封存的 UTF-8 JSON 字节计算，允许 size=0；需继续读取时使用正 size，否则停在原偏移。缺 ordinal 抛 `KeyError`，非法序号、范围、游标或登记身份抛 `ValueError`。`get` 直接还原一条完整 JSON 值，承担整条正文与解码对象的内存开销。

作为阶段结果可包装 DatasetTable(reference)，Results.page/read_record 会转发到同一批次，见[结果参考](<results.md>)。

## 存储成本与恢复

记录目录按 ordinal 保存原文起点与长度，正文为连续共享的独立压缩块。

导入成本随批次行数和正文长度增长；小记录共享块，大记录跨块。范围读取从起点直接定位必要块，页读取按 ordinal 主键继续，不扫描全部帧目录。每次公开读取建立短期 immutable SQLite 连接，作用域内缓存一个解压块，结束即清理；64 KiB 原文块不构成 SQLite 缓存或整进程内存上限。

完整恢复复制或链接登记的工件，源删除后目标可继续读取。只读完整步骤准备同样包含这些依赖，但该分析目录不能再作为恢复源。清理准备孤儿时遵守[生命周期](<storage-lifecycle.md>)的离线锁和诊断留存要求。

操作见[数据集指南](<../guide/datasets.md>)；源码见 [Datasets.import_rows](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/datasets.py#L24>)。
