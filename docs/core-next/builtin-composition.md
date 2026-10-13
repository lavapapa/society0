# 常用配方

[society0.presets](../../src/society0/presets.py) 提供 rule_preset 与 llm_preset，返回普通 Plugin 元组。研究者明确配置主体、授权、领域、调度和外部资源，再通过 RunPlan/run_plan 执行。完整机制替换和多驱动映射沿 [插件合同](plugin-contract.md) 使用叶工厂。

## 一、规则

```python
from society0 import ActorRecord, DriverResult, Phase
from society0.presets import rule_preset
from society0.kernel.schedule import SequenceSchedule, schedule_plugin

def decide(session):
    return DriverResult('completed', {'actor': session.actor.id}, 'rule')

def activate(phase):
    phase.activate('a')

plugins = rule_preset(
    records=(ActorRecord('a', 'rule'),), run=decide,
    allows=lambda *args: True,
    schedule=schedule_plugin(SequenceSchedule((1, 2), (Phase('decide', activate),))),
)
```

固定名字为 interaction、results、rule_driver、actors、actors.data、runtime，持久驱动标签为 rule。Phase 返回 StepResult 或 None；调度以完整步骤序号选择下一项。恢复时继续传入包含此前步骤的完整时间线。[双轮次对话例子](../../examples/core_next/conversation_pilot.py) 使用相同入口，同时安装 work 与 commons，保留各自配对、行动和消息。

## 二、模型

llm_preset 加入 threads、llm_driver 和标签 llm。provider 与 input_builder 为必传服务引用；选择模型服务、完整认知组合、记忆和 shell 的代码由调用者提供。

```python
from society0.presets import llm_preset
from society0.plugins import cognition_plugin, social_source_plugin
from society0.plugins.cognition import SourceSpec

plugins = llm_preset(
    records=records, allows=allows, schedule=schedule,
    provider=('provider', 'provider'),
    input_builder=('cognition', 'input_builder'),
    plugins=(
        provider_plugin, social_plugin,
        social_source_plugin(),
        cognition_plugin(sources=(SourceSpec('social.main.v1', ('social.source', 'source')),)),
    ),
)
```

片段中的 records、allows、schedule、provider_plugin、social_plugin 由研究配方明确创建。提供方可引用两段服务或三段具名 profile；配置方式见 [模型合同](models-contract.md)。来源位置、完整原文和保存后确认见 [认知合同](cognition-contract.md)。来源列表属于 cognition_plugin 的配置。

policy、extensions、shell_factory、provider_selector 原样传入既有 LLM 驱动工厂。需要记忆时，在 plugins 放入 memory_plugin、它借用的嵌入／提取／向量资源，并传入 `extensions=(('memory', 'extension'),)`。自动写入、自动召回、主动工具分别配置；interview 延续驱动的测量边界。workspace 与 shell 在使用它们的配方中显式加入。

## 三、替换

records 与 data 互斥，包括显式空 records。records 默认 None 表示空初始目录，生成器在 schema 初始化时消费。外部目录通过 `data=('actors.data', 'directory')` 加入，并将拥有该服务的 actor_data_plugin 放入 plugins；actors 复用一次目录，恢复延续持久驱动标签。目录、主体和 schema 规范见 [主体合同](actor-contract.md)。

runtime 默认连接 actors、interaction、storage、results，使用原 Runtime 的默认容量与激活上限。传入配置好的 runtime_plugin 或其他 Runtime 服务拥有者时，配方采用该根；RunPlan.runtime 指向其真实服务引用。schedule 参数为实际 Schedule 插件，RunPlan.schedule 指向对应服务。

Runner 的 Runtime 消费接口为 `async run_step(number, time, phases)`、capacity、max_activations、last_timing；拥有者通过 runtime 阶段 on_quiesce 收束。Schedule 实现 `async next_step(completed_step)`。运行清单保存实际服务引用、展开图和有效容量／预算，参见 [运行合同](runner-contract.md)。

[公共组合运行说明](../../examples/core_next/README.md) 提供完整配置、标准 runner、独立进程恢复与只读 JSON 验收；其中 runtime-replacement 场景实际替换 Runtime 和 Schedule，external-directory 场景复用明确 data 引用。

附加 plugins 保持原声明顺序，基础名字重复由 Host 拒绝。RunContract.configuration 冻结主体、来源、模型和策略；credentials 通过显式环境引用提供，运行身份与验证工件共同记录研究依据。
