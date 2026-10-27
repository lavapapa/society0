# Next 性能探针

这里保存公开内核的合成数据探针，度量存储、编码、数据访问及组合运行的成本。实验使用当前安装的 `society0`；原始输出留在调用方的实验目录。

## 一、执行

在仓库根目录安装开发依赖后，运行已纳入公开 CI 的实验：

```sh
uv run --no-sync pytest tests/experiments -m 'not real_e2e'
```

独立探针通过模块运行，参数由各自 `--help` 给出：

```sh
uv run --no-sync python -m benchmarks.core_next_storage_cost --help
uv run --no-sync python -m benchmarks.core_next_compression_probe --help
uv run --no-sync python -m benchmarks.core_composition_cost --help
```

## 二、解释

比较时保持相同业务值、读写次数和恢复语义，并分别记录 Python 与原生内存、编码、存储和服务等待。墙钟时间用于观察；确定性测试检验信息完整性和随数据增长的工作量。`json_stream_baseline.py` 提供流式编码实验的固定 Python 对照，生产路径使用内核的原生编码实现。

具体结论应随实验环境、输入与原始输出记录，避免把单次测量提升为跨平台绝对阈值。
