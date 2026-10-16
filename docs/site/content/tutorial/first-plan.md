# 创建自己的运行计划

这一课把首次运行变成自己的 Python 工厂。仍让 Alice 和 Bob 在两个时点返回决定，重点是找到主体、规则、阶段和测量分别写在哪里。先完成[第一次运行](../start/quickstart.md)，保留它生成的配置。

## 一、保存完整工厂

在仓库根目录创建 `my_simulation.py`，保存以下完整代码。这个文件导出 `build(config)`，由运行器调用。

```python title="my_simulation.py"
import sys

from society0 import ActorRecord, DriverResult, Phase, StepResult
from society0.plugins import (
    actor_plugin, interaction_plugin, results_plugin,
    rule_driver_plugin, runtime_plugin, schedule_plugin,
)
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.schedule import SequenceSchedule, activate


def build(config):
    start, end = config["start"], config["end"]
    actors = ("alice", "bob")

    def rule(session):
        return DriverResult("completed", {
            "actor": session.actor.id,
            "time": session.moment.time,
        }, "rule")

    async def decide(context):
        decisions = await activate(context, actors)
        return StepResult(
            metrics={"decisions": len(decisions)},
            tables={"decisions": (item.result.value for item in decisions)},
        )

    plugins = (
        interaction_plugin(lambda *args: True),
        results_plugin(),
        rule_driver_plugin(rule),
        actor_plugin(
            {"rule": ("rule_driver", "factory")},
            records=[ActorRecord(name, "rule") for name in actors],
        ),
        runtime_plugin(
            actor_service=("actors", "actors"),
            information=("interaction", "information"),
            actions=("interaction", "actions"),
            store=("storage", "store"),
            results=("results", "results"),
            max_activations=len(actors),
        ),
        schedule_plugin(SequenceSchedule(
            range(start, end + 1), [Phase("decide", decide)],
        )),
    )
    return RunPlan(plugins, RunContract(
        release=config["release"],
        dependencies={"python": sys.version},
        configuration={"actors": list(actors), "driver": "rule"},
        time={"start": start, "end": end},
        budgets={"max_activations": len(actors)},
    ))
```

工厂创建服务和计划，尚未推进时间。`rule` 取得绑定当前主体的 Session；`decide` 等待本阶段主体执行完毕，把返回值组成结果表。这里的授权函数开放教学环境全部交互；引入私有资料时应按[信息指南](../guide/information.md)制定授权。

## 二、运行并查看

沿用快速开始配置，给自己的程序选择新输出目录：

```bash
uv run python -m society0.kernel.runner \
  --factory my_simulation:build \
  --config runs/hello/config.json \
  --output runs/hello/my-run
uv run python -m society0.kernel.observation runs/hello/my-run
```

运行器返回 `status="completed"`、`complete_step=2`。把上一课读取脚本中的路径改为 `runs/hello/my-run`，会再次看到 Alice 和 Bob 在时间 2 的两条记录。

`--factory` 中冒号左侧是可导入模块，右侧是函数。程序放在当前仓库根目录时，`my_simulation:build` 就能定位它。

## 三、做一次小修改

把 `actors` 改成 `("alice", "bob", "carol")`，用 `runs/hello/three-actors` 作为新输出。再次读取结果，应得到三条记录，阶段指标 `decisions` 也为 3。

示例把每步激活预算设成主体数，因为每步恰好各激活一次。将来加入再次激活或多个主体阶段时，要根据真实研究安排重新定义预算。预算耗尽的状态见[激活概念](../concepts/activation.md)。

## 四、留下可解释的配置

`RunContract` 冻结这次实验的公开条件，实际服务由工厂装配。新增配置键后，工厂需要把它传给消费该参数的接口；运行器不会自动解释任意业务 JSON。

这里用 Python 版本展示依赖记录的位置。正式实验还应保存所用锁文件和研究脚本的源码身份。模型密钥从环境取得，公开合同记录凭据来源的名称。

现在进入[主体与驱动](actors.md)，给两位主体加入不同背景，并学习如何保持持久身份。
