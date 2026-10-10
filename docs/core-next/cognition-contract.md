# 认知输入

CognitiveInput 把主体背景、环境说明、感知精度、当前主观状态、阶段信号与提醒组成完整模型输入，并通过感知提供方的持久游标追加新材料。所有已输入消息留在同一 Thread；感知提供方负责自身增量位置的语义。

## 一、使用

构造 `CognitiveInput(threads, perception, consumer='operating_context', environment='', precision=None, reminders=None)`，作为 LLMDriver 的 input_builder。默认消费 ActorStore 返回的 ActorRecord：persona 保留整个原值，可包含类型背景和实例背景；state 为本次实际激活取得的完整主观状态。environment、precision、reminders 可以是原值或同步、异步 session 回调。

感知回调 `perception(session, position)` 返回 `(messages, next_position)`。messages 为本轮需要追加的完整消息列表，首轮 position 为 None。后续位置由提供方解释，例如消息序号或组合水位；框架不扫描历史推算新增内容，也不裁剪提供方返回的原文。当前状态和本轮提醒在每次实际激活加入；persona、环境说明和精度在此 Thread 首次消费时加入，此后每次重新求当前材料，与已有输入事件引用的原文比较；变更时追加新原文，未变时保留此前消息。

## 二、保存

`InputBatch(messages, consumer, cursor, context=None, *, effects=())` 是显式输入结果。LLMDriver 调用 ThreadStore.append_input，在同一短事务内追加所有消息并推进该 Thread、consumer 的游标；任一消息编码失败会回滚两者。游标正文使用与 Thread 相同的分块正文存储，索引保留对应事件序号及最近上下文消息的引用，不在每轮 cursor 复制完整背景。普通返回消息列表的 input_builder 仍可作为没有持久增量位置的轻量构建方式。

`effects` 是随批次携带的同步或异步回调，接收 ActivationContext。构建输入时不执行；LLMDriver 对最终选中的批次成功执行 `append_input` 后，将这些回调追加到既有 preparations，并由 `ActivationContext.prepare` 执行。被丢弃的批次与追加失败的批次不产生呈现效果。效果失败使激活失败，完整步骤继续遵守 Runtime 的失败边界；Thread 追加与领域效果各自遵守其事务边界。

同一主体、同一 Moment、同 mode 使用持久 Thread 定位，因此阶段 A→B→A 与独立进程恢复都能读回原消费位置。Runtime 的临时 cursors 字典仅负责当前激活引用，不承担感知位置恢复。CognitiveInput 的位置封套区分尚未初始化与提供方合法返回 None，避免把空位置误当作首次会话。

## 三、边界

动态信息的可得范围仍由绑定主体的 Information 与感知提供方决定。此构建器没有创建跨网络请求的数据库读事务，返回材料也不宣称对应任意历史 World 快照。provider 可返回实际 revision 供主体理解信息时间。

确定性测试验证完整背景和大原文进入实际提供方输入、阶段往返继续位置、独立进程恢复继续位置，以及消息失败不推进消费位置。模型 profile 的主体、类型与单次覆盖选择由提供方选择接入单独验收；这些测试不替代真实模型认知质量评估。

## 认知扩展与文件

Driver 通过 ActivationContext 和 activation_scope 组合有序异步上下文管理器。扩展进入后，输入构建器生成实际初始认知，延后 preparations 再构造需要该输入的材料。输入原文和追加材料继续进入完整 Thread，同时可从 `/context/messages.json` 读取；文件化不替换原有必要输入。

扩展提供的具名只读挂载进入 `/context/<name>`，主观笔记保存在 `/workspace`。规则驱动可直接消费结构化材料，并把实际经历交给 context.experience；LLM 的经历水位来自原 Thread。MemoryExtension 是该机制的一种实现，自动召回、自动写入和主动访问独立配置。
