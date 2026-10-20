# Next 检查点基线

这个探针通过当前 `StageStore` 公共入口测量实际运行目录；历史记录与活动投影分表，固定本步新增事实和更新数，独立扩大无关历史。它用于决定后续存储优化的方向。

## 一、运行

先安装本仓库开发环境。在源码根目录执行，每次选择新的输出目录：

```sh
uv run --no-sync python -m benchmarks.core_next_checkpoint_baseline \
  --output /absolute/task-evidence/checkpoint-history-01 \
  --history 1000 10000 100000 --active-rows 8 --steps 8 \
  --delta-rows 8 --body-bytes 128 --artifacts-per-step 1
```

每个历史规模由独立进程执行，保存实际 root/current、changesets、完整步骤描述符、工件、恢复目录与只读视图。运行结果写入输出目录的 `report.json`，控制台只输出结果位置。报告记录源码提交、工作树是否有修改、实际包导入路径、Python 与 SQLite 版本。

另一次运行可以固定历史规模、改变 `--steps` 来检查链长成本；改变 `--delta-rows` 检查真实新增工作量。百万行或更大的世界按工作区运行资产约定放在服务器，本地采用小型验收规模。

## 二、口径

探针分别测量根初始化、短事务、不可变工件准备、完整点发布、关闭、冷 writer open、只读完整点准备、可写恢复与恢复后继续提交。每段记录墙钟和 CPU，逐步记录 changeset 字节、实际新增事实与投影更新数；磁盘按 root、current/WAL、changesets、artifacts、steps 分类报告逻辑字节与分配字节。

验证独立计时，不混入业务事务和完整点发布耗时。验证逐行检查事实编号、主体、数量、正文、活动余额、工件原文，以及完整运行和恢复运行的完成水位。恢复后另提交一步，再物化该完整点确认提交可用。

内存为每个 worker 进程的 RSS 历史高水位，包含导入、验证和恢复；它不能解释为每个阶段的独立分配量。文件系统缓存未清除，因此打开/恢复的计时可能包含热缓存收益。分配字节的硬链接去重仅在同一分类内进行，跨运行目录的物理空间需另行统计。

正文使用重复的 `x` 字节，压缩性较高。这一组结果反映固定布局下的历史规模轴，真实文本的编码/压缩表现需另组负载验证。探针不包含模型、记忆端点或产业经营计算。

## 三、验证

```sh
uv run --no-sync python -m pytest \
  tests/experiments/test_core_next_checkpoint_baseline.py -q
```

确定性测试确认使用真实完整点、只读物化、恢复、工件与继续运行；改变无关历史保留相同本步工作量。测试不使用依赖机器速度的阈值。性能结论在实际报告中绑定工作负载和机器，后续优化用同一检查点做前后对照。

当前源码已采用 `changeset_stream` 导出，恢复把 `stream.read` 作为 APSW 的读取回调，根建立通过 SQLite backup 完成。[存储成本归属实验](next-storage-cost.md)复用此路径，分别测量原生 Session 内存、行布局、备份与链校验成本。
