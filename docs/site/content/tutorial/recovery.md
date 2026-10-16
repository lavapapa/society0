# 恢复与建立分支

上一课只是在读结果，这一课动手改运行。一个完整步骤可以作为继续运行或另开对照实验的起点，这里从快速开始的第 1 步建起一条新运行，共同的历史原样带过去，剩下的第 2 步由新运行接着执行。源产物是 `runs/hello/run`，新产物放在 `runs/hello/resumed`。

## 先看源运行有哪些完整步骤

先查询源运行：

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

快速开始已经跑完两步，所以第 1 步和第 2 步都有完整状态。这里选第 1 步，好观察恢复之后剩下的时点怎么执行。真遇到故障时，还要结合原始事实和 Thread 判断哪一个完整步骤可信。

## 换新目录，加上来源和步骤

工厂不变，时间范围仍写完整的 `start=1, end=2`，另外加上来源目录和步骤：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/hello/config.json \
  --source runs/hello/run --step 1 \
  --output runs/hello/resumed
```

调度器从已经完成的步骤往下走，恢复后的下一次执行是业务时点 `2`。新的 `run_id` 标识这条分支，原目录里的事实不动。读新目录第 2 步的结果，两个主体的时间都是 `2`。

!!! note "保留完整时间序列"
    `SequenceSchedule` 按完整步骤编号定位下一项。恢复配置仍包含起始时点；把配置直接截成剩余区间，可能让步骤位置和业务时间失去对应关系。

## 完整程序：比较两条运行

把下面这份完整程序保存为 `compare_recovery.py`，再执行 `uv run python compare_recovery.py`。它分别从两条运行里读出决定值，再逐项比较。

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

两步都应该比得过。第 1 步来自共同起点，第 2 步是新运行重新执行的。这里比的是确定性规则的结果；换成真实模型，后续输出可能变化，比较时要记下模型参数和研究条件。

## 不经命令行，用 run_plan 恢复

下面这份完整脚本可以保存到仓库根目录直接执行，前提是快速开始已经留下源运行和配置。

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

## 从同一个完整点开两条运行

要研究一次中途干预，可以从同一个完整点各建一条运行，把干预条件分别写进两条运行的公开配置。比较时先确认两边共同的历史、业务时点和测量口径一致。配置里改了什么，还要由运行工厂真的接到对应机制上。

如果起始假设要换，或者已经发生的历史事实本身需要改，就为新条件单独初始化一条运行。只是数据分析和展示上的修正，可以从原始工件重新生成结果。下一步阅读[实时观察](observation.md)，分清运行中的状态和已经完成的步骤。
