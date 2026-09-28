# 实际检查点的内存与查询测量

本记录使用已有实验的真实完整 root，检验合成负载未覆盖的对象驻留和磁盘成本。原实验目录保持只读，转换产物位于本任务独立目录。初测暴露的新格式与查询索引空间成本已推动后续实现，结果由[统一验收报告](acceptance-report.md)给出交付判断。

## 一、来源

来源为 simulation 的 `runs/lithium-accepted-repetitions-20260922/R3/8pct/attempt-003`，完整 step 0，checkpoint `5e3da34c16c34615abaa0e5555eb2766`。原 gzip 为 173,829,194 字节，解压 JSON 为 1,879,249,233 字节。旧格式使用原部署 Society0 4.1.12、提交 `7e98f11dcc4d5e0f5ace6ed73edca509d7e8e17a` 读取；新格式使用本轮独立源码快照。

运行前 MemAvailable 约 1.98 TB，各阶段在独立进程测量。新格式转换依据原持久化 schema 保持所有状态值；范围为 state-only，不迁移 Thread 或 Chroma。新产物位于 `runtime-observation-real-tests-20260929/converted-state`，检查点 `61b5daef7d7e44918679df096b44f7c2`。全值比较结果为 true，见[比较记录](real-state-verify.json)。比较进程同时保有两份状态的 10.218 GB 峰值不计入恢复测量。

## 二、内存

原 gzip 流式解压 3.142 秒，峰值 34.47 MB；JSON 物化 31.841 秒，保留 5.610 GB、峰值 9.890 GB。旧完整状态恢复 26.056 秒，保留 5.369 GB、峰值 10.995 GB。旧 World 构建 28.462 秒，保留 5.369 GB、峰值 10.995 GB，未观察到 World 包装再增加一份完整常驻状态。各数据见 [解压](real-root-decompress.json)、[JSON](real-root-json.json)、[恢复](real-root-state.json)和[World](real-root-world.json)。

新格式独立恢复 34.136 秒，保留和峰值均约 5.638 GB；metadata resolve 在新进程中为 0.579 秒、峰值 43.54 MB。旧 metadata resolve 为 27.022 秒、峰值 10.996 GB。新实现省去了身份查询时的全状态物化，但状态恢复的常驻对象仍然存在。见[新恢复](real-state-restore.json)、[新身份查询](real-state-resolve.json)和[旧身份查询](real-root-resolve.json)。

按原 schema 分类的 Python 对象尺寸中，replaceable 约 3.825 GB，占 90.9%；append_only_map 约 0.305 GB，占 7.3%，其余约 0.077 GB。最大的类别包括主体记录索引树、账户、交易、生产和库存。统计去重对象身份并流式统计 JSON 字节，未创建完整 JSON 字符串。对象尺寸与进程 RSS 口径不同，见[完整分组](real-state-profile.json)。这些数据支持继续研究可替换投影的按需后端；直接将不可变历史移出内存无法覆盖当前主体积，产业投影语义调整也超出本次通用引擎范围。

## 三、初测

新 root 发布耗时 84.972 秒，组件 511,270,912 字节，约原 gzip 的 2.94 倍。发布前已经完成原 JSON 解码，该进程此前峰值约 9.903 GB，不能用该高水位推断写入阶段的额外峰值。该真实负载结果与低熵合成基准分开保留，见[转换记录](real-state-convert.json)。组件保留供压缩粒度和元数据诊断，不将合成负载的空间改善外推到此产物。

首次查询索引构建 108.999 秒，本地 main/WAL/SHM 分别为 679.186、683.306、1.343 MB；查询进程峰值 54.41 MB。projections 页面返回总数 925,560、61,577 字节，读取 0.194 秒。main 与 WAL 为观测时分别计量，未用强制 TRUNCATE 隐藏追赶峰值。首次索引临时目录在测量后自动清理，诊断复验另建保留目录。见[查询初测](real-state-query.json)。

实际产物证明了读端可以避免物化整个 World，也揭示了需要进一步处理的文件体积。后续优化必须继续使用同一真实状态逐值比较，并保留这组初测作为对照。


## 四、v3 复验

v3 对同一原始 root 和原 schema 重新发布完整状态，组件为 332.94 MB，用时 90.20 秒。相比 v2 的 511.27 MB 减少约 34.9%，发布耗时增加约 6.15%；相比原 gzip 173.83 MB，文件仍约 1.92 倍。该成本包含可定位正文、压缩键字典与按业务顺序读取的元数据流，最终保留 level 3 以平衡写入和空间。见[v3 发布](real-state-convert-v3.json)，压缩与布局细分见[存储机制报告](storage-v3-real-keys-20260929.md)。

新目录 `runtime-observation-storage-20260929/converted-state-v3` 的完整 checkpoint 为 `f3284ea9d83646928d24178aa9848ebe`。独立新进程 metadata resolve 为 0.370 秒、峰值 46.37 MB；完整恢复为 34.117 秒、峰值和保留均约 5.638 GB。与 v2 恢复基本相同；相对旧原格式，消除了恢复过程约一份状态的临时峰值，常驻 World 仍保持完整内容。见[身份读取](real-state-resolve-v3.json)与[恢复](real-state-restore-v3.json)。

v3 与原始状态完整逐值比较再次通过，见[完整等值](real-state-verify-v3.json)。比较阶段为 40.494 秒，另有原 gzip 解码准备阶段；同时保有两份状态约 10.219 GB 属于测试比较开销。最终查询布局的冷建立、并行 status 与热页记录由查询专项报告归档。

本组证据支持有界编码和独立观察读取，能够具体区分临时副本、完整活动状态以及派生索引的成本。状态数据中约九成为原 schema 的 replaceable 项，进一步降低其常驻内存需要单独验证按需访问后端与产业投影的使用方式，本轮未改变这些业务语义。
