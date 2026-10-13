# 认知输入

CognitiveInput 把主体背景、环境说明、感知精度、当前主观状态、阶段信号与提醒组成完整模型输入，并通过感知提供方的持久游标追加新材料。所有已输入消息留在同一 Thread；感知提供方负责自身增量位置的语义。

## 一、使用

构造 `CognitiveInput(threads, perception, consumer='operating_context', environment='', precision=None, reminders=None)`，作为 LLMDriver 的 input_builder。默认消费 ActorStore 返回的 ActorRecord：persona 保留整个原值，可包含类型背景和实例背景；state 为本次实际激活取得的完整主观状态。environment、precision、reminders 可以是原值或同步、异步 session 回调。

感知回调 `perception(session, position)` 返回 `(messages, next_position)`。messages 为本轮需要追加的完整消息列表，首轮 position 为 None。后续位置由提供方解释，例如消息序号或组合水位；框架不扫描历史推算新增内容，也不裁剪提供方返回的原文。当前状态和本轮提醒在每次实际激活加入；persona、环境说明和精度在此 Thread 首次消费时加入，此后每次重新求当前材料，与已有输入事件引用的原文比较；变更时追加新原文，未变时保留此前消息。

## 二、保存

`InputBatch(messages, consumer, cursor, context=None, *, effects=())` 是显式输入结果。LLMDriver 调用 ThreadStore.append_input，在同一短事务内追加所有消息并推进该 Thread、consumer 的游标；任一消息编码失败会回滚两者。游标正文使用与 Thread 相同的分块正文存储，索引保留对应事件序号及最近上下文消息的引用，不在每轮 cursor 复制完整背景。普通返回消息列表的 input_builder 仍可作为没有持久增量位置的轻量构建方式。

`effects` 是随批次携带的同步或异步回调，接收 ActivationContext。构建输入时不执行；LLMDriver 对最终选中的批次成功执行 `append_input` 后，将这些回调追加到既有 preparations，并由 `ActivationContext.prepare` 执行。被丢弃的批次与追加失败的批次不产生呈现效果。效果失败使激活失败，完整步骤继续遵守 Runtime 的失败边界；Thread 追加与领域效果各自遵守其事务边界。

同一主体、同一 Moment、同 mode 使用持久 Thread 定位，因此阶段 A→B→A 与独立进程恢复都能读回原消费位置。Runtime 的临时 cursors 字典保存当前激活引用。单来源 CognitiveInput 保持既有 tuple 协议及合法 None 位置，初始化 context 与消费游标由 Thread 分别保存。

### 2.1 静态多来源

`from society0.plugins import cognition_plugin` 装配多个具名来源；值类型见 [cognition](../../src/society0/plugins/cognition.py)。`SourceSpec(id, source=(plugin, service))` 定义持久身份和显式服务引用，插件的 requires 自动合并 Thread 与全部来源插件。ID 非空且唯一，引用含两个非空名称；工厂创建时检查配置，Host 检查缺失插件/服务。

来源服务是同步或异步 `read(session, previous) -> SourceBatch(messages, position, effects=())`。首轮 previous 为 None，之后为 `SourcePosition(raw_position)`；因此合法 None、0、空映射与从未保存有明确区分。位置须可被 Thread JSON 编码；来源读旧位置后返回新值，消息和位置在交付后保持只读。

CompositePerception 按声明顺序逐个读取，将消息、效果和 `{source_id: raw_position}` 合并。旧位置做一次浅复制，保留当前未配置来源的全部键；相关领域数据继续由来源保存。ComposedInput 每次用局部 perceive 闭包调用一次 CognitiveInput，再通过 dataclasses.replace 将本次 effects 交给最终 InputBatch。背景、精度、状态、信号和提醒沿同一基础构建路径进入，全部列表属于本次调用，支持共享组合器的并发主体。

默认 consumer 为 operating_context，持久 cursor 形状为 `{'position': {'social.main.v1': {...}, 'notice.main.v1': 3}}`。来源顺序、启用集合和语义变化通过新 Run 配方选择；省去来源后保留原键，重新加入继续旧水位；改变位置语义分配新 ID。首次采用新组合从新根开始，旧单来源游标没有自动转换。

### 2.2 保存后确认

SourceBatch 的 effects 只随最终批次返回，来源不把它们直接加入 activation.preparations。第二个来源失败、整个批次丢弃或 context/messages/cursor 编码失败时，来源确认均未执行。确认运行时，本批输入与全部位置已可从 Thread 查询；provider 还未收到请求。这个边界是 Thread 保存完成。领域 effects 仍可能失败，完整步骤由 Runtime 决定是否发布；成功保存输入和成功完成 provider/整步分别记录。

[持久通知示例组件](../../examples/core_next/composition_support.py) 使用 actor/id 范围索引取得全部新增正文，通过 effects 在保存后写入 `(actor, thread_id, notice_id)` 回执。每个 data 实例以具名插件名限定 SQL 表。消息原文与回执分别保存在共享 SQL；重复确认同一主体/Thread/通知触发主键错误，确认事务整体回滚。SourcePosition 内只有最后一条通知 id，不携带 callbacks 或完整历史。

## 三、边界

动态信息的可得范围仍由绑定主体的 Information 与感知提供方决定。此构建器没有创建跨网络请求的数据库读事务，返回材料也不宣称对应任意历史 World 快照。provider 可返回实际 revision 供主体理解信息时间。

确定性测试验证完整背景和大原文进入实际提供方输入、阶段往返继续位置、独立进程恢复继续位置，以及消息失败不推进消费位置。模型 profile 的主体、类型与单次覆盖选择由提供方选择接入单独验收；这些测试不替代真实模型认知质量评估。

## 认知扩展与文件

Driver 通过 ActivationContext 和 activation_scope 组合有序异步上下文管理器。扩展进入后，输入构建器生成实际初始认知，延后 preparations 再构造需要该输入的材料。输入原文和追加材料继续进入完整 Thread，同时可从 `/context/messages.json` 读取；文件化不替换原有必要输入。

扩展提供的具名只读挂载进入 `/context/<name>`，主观笔记保存在 `/workspace`。规则驱动可直接消费结构化材料，并把实际经历交给 context.experience；LLM 的经历水位来自原 Thread。MemoryExtension 是该机制的一种实现，自动召回、自动写入和主动访问独立配置。
