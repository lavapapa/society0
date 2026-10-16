# 认知输入与来源组合

`CognitiveInput`、`InputBatch` 位于 `society0.kernel.cognition`，多来源的值类型位于 `society0.plugins.cognition`，工厂可以从 `society0.plugins` 导入。这组接口负责拼出一次激活实际交给模型的输入，并记住每个消费方读到了哪里。构建器由你提供，它按消费位置读取来源；消费位置会随输入一起保存，因此必须能编码成 JSON。

## 单来源输入与增量位置

输入构建器把 Thread 和感知服务接在一起，交给 LLMDriver 的 input_builder 使用。

<!-- api: society0.kernel.cognition.CognitiveInput -->
<!-- api: society0.kernel.cognition.InputBatch -->

感知函数接收 `(session, position)`，可以同步也可以异步，返回 `(messages, next_position)`。第一次调用时位置是 None，之后按该 Thread 和 consumer 已经保存的位置继续读。位置必须能编码成 JSON。构建器还要求 Session.cursors 里已经设置 thread_id，并且 Actor.config 里有 persona 和 state；普通运行时这些条件由驱动和 ActorStore 提供。

每次激活都会加入完整的主观状态、来源新增的消息、信号和提醒。人格、环境、精度会和已保存的输入上下文比较，有变化就追加新的原文；此前的消息继续保留。environment、precision 和 reminders 既可以是值，也可以是接收 session 的回调。读取完整人格、状态和 Thread 的开销取决于正文实际有多大。

InputBatch 交出去以后，消息和位置就不再变化。LLMDriver 用 append_input 在同一个短事务里保存消息、上下文和游标；编码失败会回滚这次追加。如果不需要持久记录增量位置，自定义 input_builder 直接返回消息列表即可。

## 组合多个来源

每个具名来源各自推进位置，装配时用服务引用表达依赖。

<!-- api: society0.plugins.cognition.SourceSpec -->
<!-- api: society0.plugins.cognition.SourcePosition -->
<!-- api: society0.plugins.cognition.SourceBatch -->
<!-- api: society0.plugins.cognition.CompositePerception -->
<!-- api: society0.plugins.cognition.cognition_plugin -->

```python
from society0.plugins.cognition import SourceSpec, cognition_plugin

plugin = cognition_plugin(sources=(
    SourceSpec('notices.v1', ('notices', 'perception')),
    SourceSpec('social.v1', ('social', 'perception')),
))
```

这段片段要求相应的服务已经装配好。每个来源接收 `(session, previous)`：还没消费过时 previous=None，消费过则是 SourcePosition，它内部的 position 合法地可以是 None、0 或空映射，返回 SourceBatch。来源按声明顺序依次读取，位置保存在 source-id 到位置的映射里；临时移除某个来源，它的旧键仍然保留，重新加回来就从原位置继续。位置的含义变了就换一个新 ID，旧单来源游标不会自动转换。

ID 必须非空且唯一，引用必须包含两个非空名称，配置不合法抛 `ValueError`。某一个来源出错会终止整个输入构建，不会只保存一半。组合器只用本次调用的局部消息和效果列表，因此可以被多个主体并发使用；来源本身仍要满足各自的并发要求。

## 输入保存之后再执行的效果

领域上的呈现确认和输入构建分开，确认发生时所用的输入已经先保存下来。

<!-- api: society0.kernel.activation.ActivationContext -->
<!-- api: society0.kernel.activation.activation_scope -->
<!-- api: society0.kernel.activation.ActivationContext.prepare -->

SourceBatch.effects 或 InputBatch.effects 是接收 ActivationContext 的回调，可以同步也可以异步。构建阶段不执行它们；只有最终批次成功 append_input 之后，这些回调才进入 preparations，并在向提供方发请求之前执行。被丢弃或追加失败的批次不会有确认效果。效果抛异常会让这次激活失败；Thread 追加和领域效果各自遵守自己的事务边界，整个步骤按 Runtime 的失败策略处理。

扩展按声明顺序进入异步上下文管理器，退出时逆序。context.mounts 用来登记只读资料，context.experience 保存规则驱动实际经历过的内容。记忆的写入、召回和主动访问由各自的策略配置。来源读取时借用的数据库 view 必须在同步回调内结束，任何 await 之前先把需要的值取成独立对象。

继续阅读[认知指南](<../guide/cognition.md>)；源码见 [CompositePerception](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/cognition.py#L48>)。
