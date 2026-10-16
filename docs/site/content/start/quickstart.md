# 第一次运行

这个例子让 Alice 和 Bob 在两个业务时点各作一次规则决定，并把主体名与时间保存成表。你会完成从启动仿真到读取结果的整条路径，全程无需模型服务。

## 一、写入运行配置

在[安装](installation.md)完成的源码仓库根目录执行以下命令。配置保存本次源码身份和业务时间范围；这里的 `start` 与 `end` 两端都包含。

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

运行目录保存实际结果，配置文件保存运行所需的公开参数。每次重新尝试时，给 `--output` 选择一个新的目录名。

## 二、启动两个主体

仓库已经提供了完整的规则运行工厂。CLI 加载工厂，让它把配置转换成 `RunPlan`，再执行这份计划。

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

观察输出中的 `complete.step`。它表示已发布的完整步骤，后续分析与恢复都可以使用这个边界。

## 三、读取实际结果

下面的完整脚本准备第 2 步的只读视图，读取规则决定表。可以保存为仓库根目录的 `inspect_hello.py`，再执行 `uv run python inspect_hello.py`。

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

这里读取的是第 2 步，第一步的记录仍保存在运行中。这个小表可以在一页内读完；大结果的完整分页和正文读取见[记录与读取结果](../tutorial/results.md)。

如果启动时提示输出位置已经存在，请为新尝试更换 `--output`，并相应更新读取脚本中的路径。若运行没有发布第 2 步，先读[运行观察](../tutorial/observation.md)定位失败；保留原目录以便检查。

## 四、理解发生了什么

运行工厂创建了两个 Actor，用规则 Driver 返回决定值。阶段函数激活主体，把决定组织成 `StepResult`；Schedule 提供两个业务时点，Runtime 执行并发布完整步骤。结果读取使用已保存的工件，无需再次激活主体。

现在你可以阅读[核心概念](concepts.md)，然后通过[创建自己的运行计划](../tutorial/first-plan.md)修改主体和规则。想先尝试继续运行，可以直接进入[恢复与分叉](../tutorial/recovery.md)。
