# 组合多个认知来源

主体要同时收到通知、资料更新和社交推荐时，每个来源都得记住自己读到哪，输入构造器再按你指定的次序把消息追加进同一个 Thread。先准备 threads 服务、主体目录和驱动，基础接线见[LLM 教程](../tutorial/llm.md)。

## 写一个最简单的来源

下面是一个完整的来源插件定义，保存为 `study_source.py` 后加进自己的 RunPlan。它在每个 Thread 第一次读取时交付研究说明，之后保持原来的位置。

```python title="study_source.py"
from society0.kernel.plugins import Plugin
from society0.plugins.cognition import SourceBatch


def brief_source_plugin(name="study.brief"):
    def install(context):
        def read(session, previous):
            if previous is not None:
                return SourceBatch([], previous.position)
            message = {"role": "user", "content": "核对资料的来源与发布日期，保留判断依据。"}
            return SourceBatch([message], {"delivered": True})
        context.provide("source", read)
    return Plugin(name, install=install)
```

来源拿到的是 `SourcePosition | None`：第一次为 None，之后从 `previous.position` 取该来源上次保存的值。返回的 messages 保留原文，position 必须是可以持久保存的值。真实的资料来源可以把最后读到的记录位置当作水位，读取同一批资料时据此定出下一个位置。

## 把来源按次序绑进计划

下面这段装配片段加进已有计划。它要求同一主机里有 threads 和一个命名为 social 的社交配方，装社交机制的条件见[社交指南](social.md)。

```python
from society0.plugins import cognition_plugin, social_source_plugin
from society0.plugins.cognition import SourceSpec
from study_source import brief_source_plugin

sources = (
    brief_source_plugin(),
    social_source_plugin(),
    cognition_plugin(
        sources=(
            SourceSpec("brief.v1", ("study.brief", "source")),
            SourceSpec("social.main.v1", ("social.source", "source")),
        ),
        environment="参与者共享资料和讨论空间。",
    ),
)
```

把 `sources` 展开加进插件序列，驱动绑定 `input_builder=("cognition", "input_builder")`。SourceSpec 的 id 就是持久位置用的键，服务引用指向真正的来源；同一个组合里每个 id 必须唯一。来源在列表中的先后决定这次追加消息的次序。改了 id 名字，位置身份也跟着变，要当成一份新配方记下来。

## 处理写入 Thread 之后的效果

构造输入和确认主体已经收到输入是两个时刻。来源可以把要确认的操作放进 `SourceBatch.effects`；LLMDriver 把输入追加到 Thread 之后，再把这些效果交给激活准备流程。社交来源正是用这条路径登记推荐的实际呈现，所以单独预览来源不会增加曝光。

自己写来源时，把完整消息、下一个位置和需要确认的效果一起返回，免得在一次普通预览里就把通知消费掉或记上曝光。效果对象要符合[认知参考](../reference/cognition.md)里的激活准备协议；复用现成的社交来源，比另写一套确认流程更直接。

## 核验增量与恢复

用一次短实验按顺序检查四件事：第一次激活收到所有来源的首批消息；同一 Moment 里再次激活只追加新材料；来源 A 更新时来源 B 的位置不动；从完整步骤恢复后位置接着往下走，旧 Thread 的原文还在。用[观察教程](../tutorial/observation.md)读实际 Thread，把消息和来源位置对一遍，不要只看最终回答就判断认知是否完整。

游标失效时，重新读取该来源，并按新返回的版本继续，同时保留失败诊断。改动来源顺序、启用状态或位置含义时，另记一份新的运行配方。具体实现见 [SourceSpec 与 cognition_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/cognition.py#L11-L98)；可运行的多来源和恢复案例见[示例索引](examples.md)。
