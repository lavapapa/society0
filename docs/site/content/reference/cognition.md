# 认知输入与来源组合

`CognitiveInput`、`InputBatch` 位于 `society0.kernel.cognition`；多来源值类型位于 `society0.plugins.cognition`，工厂可从 `society0.plugins` 导入。此接口构造实际输入及持久消费位置。

## 一、单来源

输入构建器连接 Thread 与感知服务，交给 LLMDriver 的 input_builder 使用。

<!-- api: society0.kernel.cognition.CognitiveInput -->
<!-- api: society0.kernel.cognition.InputBatch -->

感知函数接受 `(session, position)`，可同步或异步，返回 `(messages, next_position)`。首次位置是 None；后续从该 Thread、consumer 已保存的位置读取。位置须能 JSON 编码。构建器需要 Session.cursors 中已设置 thread_id，以及带 persona/state 的 Actor.config；普通运行由驱动与 ActorStore 提供这些条件。

每次激活加入完整当前主观状态、来源新增消息、信号与提醒。人格、环境、精度与已保存输入上下文比较，变化时追加新原文；历史消息继续保留。environment、precision、reminders 可为值或 session 回调。读取完整人格、状态和 Thread 的成本取决于实际正文大小。

InputBatch 交付后保持消息与位置稳定。LLMDriver 用 append_input 在同一短事务内保存消息、上下文与游标；编码失败回滚该次追加。直接返回消息列表的自定义 input_builder 可用于无需持久增量位置的场景。

## 二、多来源

具名来源可独立推进位置，装配依赖由服务引用表达。

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

片段要求相应服务已装配。每个来源接受 `(session, previous)`：从未消费时 previous=None；已消费时为 SourcePosition，内部 position 可以合法地为 None、0 或空映射。返回 SourceBatch。来源按声明顺序读取，位置保存在 source-id 映射中；暂时移除来源保留旧键，重新加入继续原位置。改变位置语义使用新 ID，旧单来源游标不自动转换。

ID 必须非空且唯一，引用必须包含两个非空名称，非法配置抛 `ValueError`。来源错误终止整个输入构建，不产生半批保存。组合器使用本次调用的局部消息及效果列表，可供多个主体并发使用；来源自身仍须满足其并发合同。

## 三、保存后效果

领域呈现确认与输入构建分离，保证确认所依据的输入已经留存。

<!-- api: society0.kernel.activation.ActivationContext -->
<!-- api: society0.kernel.activation.activation_scope -->
<!-- api: society0.kernel.activation.ActivationContext.prepare -->

SourceBatch.effects 或 InputBatch.effects 是接收 ActivationContext 的同步或异步回调。构建时不执行；最终批次成功 append_input 后才进入 preparations，在 provider 请求前执行。被丢弃或追加失败的批次无确认效果。效果异常使激活失败；Thread 追加与领域效果分别遵守自己的事务边界，整步按 Runtime 失败策略处理。

扩展按声明顺序进入异步上下文管理器，逆序退出。context.mounts 可登记只读资料，context.experience 保存规则驱动实际经历。记忆写入、召回及主动访问由各自策略配置。来源读取的数据库 borrowed view 必须在同步回调内结束，任何 await 前先取得需要的独立值。

继续阅读[认知指南](<../guide/cognition.md>)；源码见 [CompositePerception](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/plugins/cognition.py#L48>)。
