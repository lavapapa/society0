# 恢复与建立分支

一个完整步骤可以成为继续运行或建立对照实验的起点。本页从快速开始的第 1 步建立新运行，保留共同历史，再继续执行第 2 步。

## 一、选择完整起点

先查询源运行：

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

快速开始已经完成两个步骤，因此第 1 步和第 2 步都有完整状态。下面选择第 1 步，以便观察恢复后如何执行剩余时点。实际故障时，需要结合原始事实与 Thread 判断哪个完整步骤可信。

## 二、在新目录继续

保持原工厂和完整时间范围 `start=1, end=2`，增加来源目录与步骤：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/hello/config.json \
  --source runs/hello/run --step 1 \
  --output runs/hello/resumed
```

调度器按已经完成的步骤继续：恢复后的下一次执行是业务时点 `2`。新的 `run_id` 标识这条运行分支，原目录的事实保持不变。读取新目录的第 2 步结果，可以看到两个主体的时间都是 `2`。

!!! note "保留完整时间序列"
    `SequenceSchedule` 按完整步骤编号定位下一项。恢复配置仍包含起始时点；把配置直接截成剩余区间，可能让步骤位置和业务时间失去对应关系。

## 三、核对共同历史与新结果

保存下面完整程序为 `compare_recovery.py` 并执行 `uv run python compare_recovery.py`。它比较本例的决定值，运行身份分别读取。

```python title="compare_recovery.py"
from society0.kernel.results import Results
from society0.kernel.storage import StageReader


def decisions(path, step):
    with StageReader(path) as reader:
        result = Results(reader)
        reference = result.phase(step, 0)["tables"]["decisions"]
        return [item["value"] for item in result.page(reference)["items"]]


for step in (1, 2):
    original = decisions("runs/hello/run", step)
    resumed = decisions("runs/hello/resumed", step)
    assert original == resumed
    print("相同的决定值：", step, resumed)
```

两步都应通过比较。第 1 步来自共同起点，第 2 步由新运行重新执行。这里核对的是确定性规则结果；真实模型的后续输出可能变化，比较时需记录模型参数与研究条件。

## 四、用 Python 恢复

这是一份可保存到仓库根目录执行的完整脚本；需要快速开始已经产生源运行和配置。

```python
import asyncio
import json
from pathlib import Path
from examples.core_next.rule_run import build
from society0.kernel.runner import run_plan

config = json.loads(Path("runs/hello/config.json").read_text())
asyncio.run(run_plan(
    "runs/hello/python-branch", build(config),
    source="runs/hello/run", step=1,
))
```

恢复会重新安装运行服务，重建连接和派生资源；初始化数据来自选定完整点。插件的初始化函数会被跳过。更改 schema 后需要重新检查恢复条件，具体约束见[组合参考](../reference/composition.md)。

## 五、建立对照实验

如果你研究一个中途干预，可以从同一个完整点分别建立两条运行，并在各自公开配置中记录干预条件。比较时对齐共同历史、业务时点与测量口径。配置改动必须由运行工厂实际接入相应机制。

如果起始假设或已经发生的历史事实需要改变，应为新的研究条件初始化独立运行。数据分析展示的修正则可以从原始工件重新生成分析结果。下一步阅读[实时观察](observation.md)，区分运行中的状态与已完成步骤。
