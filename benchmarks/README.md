# Checkpoint v4 历史增长基准

从仓库根目录直接运行 A/B 基准并保存 JSON 报告：

```bash
python benchmarks/v4_incremental_checkpoint_benchmark.py \
  --history 100 1000 10000 \
  --ticks 100 \
  --output benchmarks/results/v4-history-ab.json
```

每个历史规模在独立子进程中构造 append-only 历史，再发布相同的固定
delta。报告记录当前 Tick 写入字节数、记录数、历史组件读取计数、墙钟样本
和子进程 `ru_maxrss` 增量。墙钟时间用于观察，不作为绝对阈值。

报告还包含一个可替换投影 `R` 增大的正向对照。该对照的输出变大属于预期，
用于区分“本 Tick 改写值变大”和“累计历史 H 被错误复制”。
