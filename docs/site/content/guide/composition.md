# 用配方组合与替换服务

规则、主体、结果和调度在自己那份计划里都定好之后，`rule_preset` 和 `llm_preset` 可以替你少写一遍常用接线。两者返回的还是普通 Plugin 元组，照样交给 RunPlan 执行。先完成[自己的运行计划](../tutorial/first-plan.md)，下面沿用同一批两位主体。

## 用规则配方跑一遍

把下面的完整工厂保存为仓库根目录的 `preset_study.py`，用快速开始生成的配置运行。

```python title="preset_study.py"
import sys
from society0 import ActorRecord, DriverResult, Phase
from society0.presets import rule_preset
from society0.kernel.results import StepResult
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.schedule import SequenceSchedule, activate, schedule_plugin


def build(config):
    def decide(session):
        return DriverResult("completed", {"actor": session.actor.id})

    async def phase(context):
        outcomes = await activate(context, ("alice", "bob"))
        return StepResult(tables={"decisions": (item.result.value for item in outcomes)})

    plugins = rule_preset(
        records=(ActorRecord(name, "rule") for name in ("alice", "bob")),
        run=decide, allows=lambda *args: True,
        schedule=schedule_plugin(SequenceSchedule(
            range(config["start"], config["end"] + 1), (Phase("decide", phase),)
        )),
    )
    return RunPlan(plugins, RunContract(
        release=config["release"], dependencies={"python": sys.version},
        configuration={"actors": ["alice", "bob"]},
        time={"start": config["start"], "end": config["end"]}, budgets={},
    ))
```

```bash
uv run python -m society0.kernel.runner --factory preset_study:build \
  --config runs/hello/config.json --output runs/preset/run
uv run python -m society0.kernel.observation runs/preset/run
```

运行结束后在 `complete` 里核对最终步数，再按[结果教程](../tutorial/results.md)读取 `decisions` 表。这个例子没有领域资料，`allows=lambda *args: True` 够用；一旦加入私有资料，就按[信息指南](information.md)定义授权。

## 往配方里加机制，核对展开后的插件图

自己的领域插件通过 `plugins` 参数交给配方。配方本身已经提供 interaction、results、actors 和 Runtime 等基础服务，LLM 配方还提供 threads，所以重复添加同名基础插件会被 Host 拒绝。

一个插件可以用 `includes` 静态带上子插件；展开之后每个名字仍须唯一，消费者一律用具名服务引用接线。要放两个社交实例时，分别起名 `forum_a` 和 `forum_b`，并显式指明各自的数据来源。运行目录里的 `runner.json` 记录了这份图：`plugin_roots` 是根声明，`plugins` 是展开后的 requires、schema_requires 与 includes。照着这里核对，容易发现漏接的叶插件或重复的资源拥有者。

## 换掉配方里的服务

换服务靠显式引用。要改用别处的人口来源，就给配方传 `data=("population", "directory")`，并把拥有该服务的插件加进计划；此时省略 `records`，空元组同样要省掉。详见[主体目录](actor-directory.md)。

要换 Runtime，把实际插件传给 `runtime`；插件名或服务名变了，`RunPlan.runtime` 也要指向新服务。自定义调度同理：配方接收 Schedule 插件，`RunPlan.schedule` 指向该服务。消费者协议与可执行的 runtime-replacement 场景见[公共组合运行说明](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/README.md)。

LLM 配方要求显式传入 provider 与 input_builder。下面是装配片段，其中 `model_resources`、`source_plugins`、`cognition`、`schedule`、`records` 和授权函数都由你自己的研究计划创建。

```python
from society0.presets import llm_preset

plugins = llm_preset(
    records=records, allows=allows, schedule=schedule,
    provider=("models", "models", "research"),
    input_builder=("cognition", "input_builder"),
    plugins=(model_resources, *source_plugins, cognition),
)
```

先按[模型指南](models.md)准备服务，再按[认知指南](cognition.md)组合信息。驱动标签多、或者需要替换到更细的粒度时，直接调用叶工厂；[配方源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/presets.py#L31-L58)里有全部接线，没有额外的运行层。
