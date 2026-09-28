# 检查点存储实施与验证

本记录覆盖声明式检查点的物化、按记录查询、跨目录根引用、跨步暂存及离线导出。完整 marker 保持唯一恢复边界；SQLite 采用 DELETE journal，临时文件关闭、fsync、rename 后才进入 manifest。组件明确标为 `sqlite_records_v1`，旧整块 gzip 组件不会被当作新格式静默解析。

## 一、测试

所有命令在 `.codex-worktrees/society0-runtime-observation` 执行，解释器为 `/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python`，环境为 `PYTHONPATH=src`。

`python -m pytest tests/primary/test_checkpoint_records.py -q` 按实现阶段依次出现以下红灯：`V4CheckpointStore.read_operations` 不存在；`merge_records` 不存在；带 base 的新根恢复缺少源状态 x；范围读取接口不存在；`export_bundle` 不存在；fork 调用了被测试禁止的 `restore`；大值验证调用了被测试禁止的整树 `json.dumps`。对应接口随后实现并逐项转绿。首次尝试曾使用错误的解释器路径而未启动 pytest，该次不计作行为红灯。

最终直接相关测试命令与结果保存在 `storage-focused-tests.txt`，命令包含 checkpoint_records、persistence_manager、incremental_checkpoint、branch_gc、process_crash、runtime_checkpoint、component_read、memory_pairing、memory_epoch_sharing 和 result_datasets 十个文件。失败注入覆盖暂存写失败、epoch 合并失败、marker 成功后暂存清理失败；恢复始终以完整 marker 为界。根借用测试禁止 `_plain_snapshot_value`，分叉测试禁止物化完整 World，大记录末尾读取测试限制为一个相交压缩块。

## 二、存储

小记录共享 1 MiB 原文压缩块；每条记录保存块内位置和长度，超大记录跨块保存。记录范围查询通过块起点索引定位，只解压相交块。路径前缀单独存储，记录总数和各路径总数在写入处维护，分页无需扫描全历史计数。顺序恢复复用当前解压块，避免为同块内每一条记录重新解压。

多步 checkpoint 先把每个完整步骤封存到未发布 SQLite 文件，内存保留步骤与记忆 epoch 等元数据。最终事务通过 SQL 复制压缩块、重映射原文位置与路径编号，不重新解压业务值。暂存文件自身不具有可恢复资格。失败清理不会将已完成 marker 改成失败。

跨目录恢复的根通过相对路径和固定 checkpoint_id 引用来源完整检查点，并应用初始化期间的增量。Society0 集成由观察接口负责人完成，必须覆盖恢复后至新根发布前的代理修改。同目录 fork 保留共享组件而不物化 World。

`export_bundle(destination, step=None, mode='analysis')` 递归复制状态、Thread 及已经提交的数据集引用，并重写 bundle 内的来源相对路径。`mode='restore'` 还复制记忆库；有记忆依赖时，要求运行最终状态和原 producer PID 已退出。活动 Chroma 不通过普通文件复制假定为一致快照。`bundle.json` 明确记录 scope 和 full_restore。

## 三、量级

`checkpoint_baseline_probe.py` 从只读 Git 对象 fc67432 加载旧存储，与当前存储使用相同已构造记录集。`checkpoint_records_probe.py` 单测有界写入和范围读取，`checkpoint_root_probe.py` 使用真实 World→PersistenceManager.publish_root 路径并禁止全 World 快照。结果 JSON 分别保留旧格式、逐记录压缩中间方案、共享块最终方案、结构化异质值与大根结果；被否决的中间测量保留作为设计判断证据。

重复文本样本暴露索引固定成本，结构化样本包含主体编号、日期、数量和异质文本。两者均为 204882 条，最大记录超过 2.79 MiB。大根原文约 1.899 GB，使用不同记录对象及共享不可变字符串；它检验真实发布路径的遍历和复制行为，不模拟同等体积互不相同字符串的 World 常驻内存。

RSS 来自 macOS `ru_maxrss`，单位为字节，记录发布前进程峰值、发布后进程峰值及差额。初始化验证曾整体编码并产生约 1.277 GB 历史峰值，该路径已替换为逐节点 JSON 兼容性检查；重测前峰值约 108 MB，根发布额外峰值约 6 MB。生产环境的 Python 对象常驻空间、Chroma 和模型运行不包含在纯存储基准中。

共享块格式保留可寻址索引，因此相对旧整块 gzip 仍有写入时间及磁盘成本。原文/磁盘比和旧压缩文件比均须报告，不能用低峰值内存宣称总吞吐或压缩率更优。Ceph 部署文件系统的合成验收由独立执行者补充；此处本机数字不能推断 Ceph 的 fsync 延迟。

最终本机异质样本为 138454364 字节原文，最大记录 2925626 字节。旧格式发布 1.798 秒、35272720 字节、额外峰值 RSS 467779584 字节；共享块格式发布 5.921 秒、52859934 字节、额外峰值 RSS 11436032 字节。当前文件为原文的 38.2%，为旧文件的 1.50 倍，写入时间为旧格式的 3.29 倍。共享块正文约 35.35 MB，其余约 17.51 MB 是记录定位、路径及其索引。该权衡换取逐条查询、随机范围读取和约 41 倍更低的发布额外峰值内存。

重复文本样本的旧压缩结果约 1.69 MB，共享块为 19.92 MB，其中正文块约 1.76 MB、索引等约 18.16 MB。这个最坏压缩比场景保留在报告中。被否决的逐记录 gzip 中间格式为 54.17 MB；共享块降低了 63.2% 的磁盘占用。大根真实路径重测为 1.899 GB 原文、10.28 秒发布、24.97 MB 写入、额外峰值 RSS 6.09 MB。

独立审查者 threads 新增 `test_storage_review.py` 揭示导出未核对来源 checkpoint_id；storage 复现红灯后补充与 restore 相同的身份检查，包含新测试的十二项通过。storage 对 threads 的 Thread、Memory 和 datasets 代码作非作者审查，提出冷恢复 offsets 原地截断会短暂破坏读取；threads 改为临时文件原子替换，storage 复跑相关二十二项通过。多步未提交 epoch 的全历史集合复制也在审查中发现，由 threads 实现只读 base+pending 视图，storage 接入发布器，失败清除 pending 即撤销可见性。
