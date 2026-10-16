# 组合多个认知来源

参与者可能同时接收通知、资料更新和社交推荐。每个来源应保存自己的读取位置，输入构造器把它们按明确次序追加到同一 Thread。先准备 threads 服务、主体目录和驱动，基础接线见[LLM 教程](../tutorial/llm.md)。

## 一、定义一个来源

下面是完整的来源插件定义，可保存为 `study_source.py`，再加入自己的 RunPlan。该来源在每个 Thread 首次读取时交付研究说明，后续读取保持位置。

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

来源收到 `SourcePosition | None`：首次为 None；后续通过 `previous.position` 取得该来源保存的值。返回的 messages 保留原文，position 使用可持久记录的值。真实资料来源可将最后读取的记录位置作为水位，并在读取同一批资料时确定下一位置。

## 二、绑定有序来源

以下接线片段添加到已有计划。它需要同一主机的 threads 与命名为 social 的社交配方，运行社会机制的条件见[社交指南](social.md)。

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

将 `sources` 展开加入插件序列，驱动绑定 `input_builder=("cognition", "input_builder")`。SourceSpec 的 id 是持久位置的键，服务引用指向实际来源；同一组合中每个 id 必须唯一。来源列表的顺序决定本次追加消息的顺序。重新命名 id 会改变位置身份，应当作为新配方记录。

## 三、处理保存后的效果

构建输入和确认主体收到输入分成两个时刻。来源可以把待确认操作放在 `SourceBatch.effects`；LLMDriver 将输入追加到 Thread 后，把效果交给激活准备流程。社交来源用这条路径登记实际推荐呈现，因此单独预览来源不会增加曝光。

自定义来源先返回完整消息、下一位置以及需要确认的效果，避免在普通预览中提前消费通知或记曝光。效果对象必须遵守[认知参考](../reference/cognition.md)中的激活准备协议；复用现有社交来源比另写一套确认流程更直接。

## 四、核验增量与恢复

用短实验依次检查：首次激活收到所有来源首批消息；同一 Moment 重激活追加新材料；来源 A 更新时来源 B 保持原位置；完整步骤恢复后位置继续且旧 Thread 原文仍在。通过[观察教程](../tutorial/observation.md)读取实际 Thread，比较消息和来源位置，避免仅凭最终回答判断认知是否完整。

游标失效时按来源的版本合同重新读取，保留失败诊断。更改来源顺序、启用状态或位置含义时记录新 Run 配方。具体实现见 [SourceSpec 与 cognition_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/cognition.py#L11-L98)；可运行的多来源和恢复案例见[示例索引](examples.md)。
