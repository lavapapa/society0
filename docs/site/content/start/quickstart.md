# 第一次运行

这一课在装好的环境里跑起第一场仿真：让 Alice 和 Bob 在两个业务时点各作一次规则决定，把主体名与时间存成一张表，再把表读出来。全程不需要模型服务。照做一遍，你会得到一份带完整步骤的运行目录，也看清一次运行从头到尾经过哪些环节。

## 写一份运行配置

在[安装](installation.md)完成的源码仓库根目录执行下面的命令。配置里放的是本次运行的源码身份和业务时间范围，这里的 `start` 与 `end` 两端都包含在内。

```bash
mkdir -p runs/hello
uv run python - <<'PYCODE'
import json
import subprocess
from pathlib import Path

config = {
    "release": {"commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip()},
    "start": 1,
    "end": 2,
}
Path("runs/hello/config.json").write_text(
    json.dumps(config, ensure_ascii=False), encoding="utf-8")
PYCODE
```

配置文件只放运行所需的公开参数，实际结果写进运行目录。每次重新尝试，都给 `--output` 换一个新的目录名。

## 启动这次运行

仓库里已经有一个可以直接使用的规则运行工厂。CLI 加载这个工厂，由它把配置转换成 `RunPlan`，然后执行这份计划。

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config runs/hello/config.json \
  --output runs/hello/run
```

成功返回的 JSON 中，`status` 是 `completed`，`complete_step` 是 `2`。`run_id` 是本次运行的身份，每次运行会不同。

```json title="返回值中的关键字段"
{"status": "completed", "complete_step": 2, "diagnostic_errors": 0}
```

接着读取保存的状态：

```bash
uv run python -m society0.kernel.observation runs/hello/run
```

在输出里找到 `complete.step`，它表示已经发布的完整步骤，后续的分析与恢复都从这个位置出发。

## 把结果读出来

下面这段完整程序为第 2 步准备一份只读视图，读出规则决定表。可以保存成仓库根目录下的 `inspect_hello.py`，再执行 `uv run python inspect_hello.py`。

```python title="inspect_hello.py"
from pathlib import Path
from tempfile import TemporaryDirectory
from society0.kernel.observation import Observation
from society0.kernel.storage import StageStore

with TemporaryDirectory() as temporary:
    view = Path(temporary) / "complete"
    with StageStore.prepare_readonly("runs/hello/run", view, step=2):
        pass
    with Observation(view) as reader:
        phase = reader.result_phases(step=2)["items"][0]
        header = reader.result_page(phase["reference"])["items"][0]["value"]
        page = reader.result_page(header["tables"]["decisions"])
        for item in page["items"]:
            print(item["value"])
```

预期打印下面两行。字典显示的引号样式取决于打印方式，字段值保持一致。

```text
{'actor': 'alice', 'time': 2}
{'actor': 'bob', 'time': 2}
```

这里读的是第 2 步，第 1 步的记录仍然保存在运行目录里。这张表小到可以一页读完；结果很大时怎样分页、怎样读正文，见[记录与读取结果](../tutorial/results.md)。

如果启动时提示输出位置已经存在，就为新尝试换一个 `--output`，同时把读取脚本里的路径改成新目录。运行没能发布第 2 步时，先读[运行观察](../tutorial/observation.md)定位失败原因，原目录留着不要删。

## 刚才这些环节各自做了什么

运行工厂创建了两位 Actor，规则 Driver 负责返回决定值。阶段函数在这里激活主体，把两个决定收进 `StepResult`；Schedule 给出两个业务时点，Runtime 一步步执行，最后发布完整步骤。上面那次读取只用已经保存的工件，不会再激活主体。

现在你可以阅读[核心概念](concepts.md)，然后通过[创建自己的运行计划](../tutorial/first-plan.md)修改主体和规则。想先试试继续运行，可以直接进入[恢复与分叉](../tutorial/recovery.md)。
