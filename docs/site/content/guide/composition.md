# 用配方组合与替换服务

当规则、主体、结果和调度已经明确时，使用 `rule_preset` 或 `llm_preset` 组织常用接线。两者返回普通 Plugin 元组，后续仍通过 RunPlan 执行。先完成[自己的运行计划](../tutorial/first-plan.md)，本页保持相同的两主体语境。

## 一、运行规则配方

保存以下完整工厂为仓库根目录的 `preset_study.py`，使用快速开始生成的配置运行。

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

在 complete 中核对最终步数，再按[结果教程](../tutorial/results.md)读取 `decisions` 表。`allows=lambda *args: True` 适用于这个无领域资料的小例子；加入私有资料时，按[信息指南](information.md)定义授权。

## 二、附加机制与展开图

把领域插件传入配方的 `plugins` 参数。配方已经提供 interaction、results、actors 和 Runtime 等基础服务，LLM 配方还提供 threads。重复添加同名基础插件会被 Host 拒绝。

一个插件可以通过 `includes` 静态包含子插件。展开后每个名字仍须唯一，所有消费者通过具名服务引用连接。使用两个社交实例时，分别命名 `forum_a` 和 `forum_b`，并显式选择对应的资料来源。查看运行目录的 `runner.json`：`plugin_roots` 记录根声明，`plugins` 记录展开后的 requires、schema_requires 与 includes。核对这里的实际图，能发现漏接的叶或重复的资源拥有者。

## 三、替换服务

替换采用显式引用。需要更换人口来源时，给配方传 `data=("population", "directory")` 并加入拥有该服务的插件；此时省略 `records`，包括显式空记录。详见[主体目录](actor-directory.md)。

需要更换 Runtime 时，将实际插件传给 `runtime`；若插件名或服务名改变，同时令 `RunPlan.runtime` 指向新服务。自定义调度同理：配方取得 Schedule 插件，`RunPlan.schedule` 指向该服务。消费者协议和可执行的 runtime-replacement 场景见[公共组合运行说明](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/README.md)。

LLM 配方要求明确传入 provider 与 input_builder。以下是接线片段：`model_resources`、`source_plugins`、`cognition`、`schedule`、`records` 和授权函数由研究计划创建。

```python
from society0.presets import llm_preset

plugins = llm_preset(
    records=records, allows=allows, schedule=schedule,
    provider=("models", "models", "research"),
    input_builder=("cognition", "input_builder"),
    plugins=(model_resources, *source_plugins, cognition),
)
```

先按[模型指南](models.md)准备服务，再按[认知指南](cognition.md)组合信息。拥有多个不同驱动标签或需要细粒度替换时，直接调用叶工厂；[配方源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/presets.py#L31-L58)展示全部接线，无额外运行层。
