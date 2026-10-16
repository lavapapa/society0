# 创建自己的运行计划

[第一次运行](../start/quickstart.md)用的是仓库自带的工厂。这一课把它换成你自己写的一份：Alice 和 Bob 仍在两个时点各返回一次决定，结果表也和上一课一样，差别在于主体、规则、阶段和测量分别写在哪里，现在由你的文件安排。动手前先做完第一次运行，保留它生成的 `runs/hello`，其中的 `config.json` 这一课继续用。

## 完整程序：my_simulation.py

在仓库根目录新建 `my_simulation.py`，把下面这份完整程序存进去。文件只导出 `build(config)` 一个函数，运行器通过 `--factory` 调用它。

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

`build` 返回插件集合和运行计划，这一步还没有推进时间，真正执行由运行器负责。`rule` 拿到的 Session 绑定在当前主体上；`decide` 等本阶段的主体全部执行完，再把它们的返回值收成 `decisions` 表。传给 `interaction_plugin` 的授权函数对教学环境放开全部交互，换成私有资料时，按[信息指南](../guide/information.md)收紧授权。

## 换成新输出目录再跑一次

配置直接沿用上一课生成的 `runs/hello/config.json`，只把输出换成一个新目录：

```bash
uv run python -m society0.kernel.runner \
  --factory my_simulation:build \
  --config runs/hello/config.json \
  --output runs/hello/my-run
uv run python -m society0.kernel.observation runs/hello/my-run
```

运行器返回 `status="completed"`、`complete_step=2`。把上一课读取脚本里的路径改成 `runs/hello/my-run`，会再看到 Alice 和 Bob 在时间 2 的两条记录。

`--factory` 里冒号左边是模块名，右边是函数名。文件放在当前仓库根目录时，`my_simulation:build` 就能找到它。

## 加上第三位主体，看指标怎么变

在 `my_simulation.py` 里把 `actors` 改成 `("alice", "bob", "carol")`，输出目录换成 `runs/hello/three-actors`，重新运行并读取结果。这时应得到三条记录，阶段指标 `decisions` 也变成 3。

示例把每步的激活预算设成主体数量，因为每步恰好把每位主体激活一次。将来出现同一主体再次激活，或者一个步骤里有多个主体阶段，预算就要按研究安排重新算。预算耗尽以后是什么状态，见[激活概念](../concepts/activation.md)。

## 把实验条件写进配置

`RunContract` 记下这次实验对外的条件，实际服务由工厂装配。配置里新增一个键之后，要由工厂把它传给真正消费该参数的地方；运行器不会自动理解任意的业务 JSON。

这里用 Python 版本说明依赖记在哪一项。正式实验还要保存所用锁文件和研究脚本的源码身份。模型密钥从环境取得，公开记录里只写凭据所在环境变量的名称。

现在进入[主体与驱动](actors.md)，给两位主体加入不同背景，并学习如何保持持久身份。
