# 认知输入

CognitiveInput 把主体背景、环境说明、感知精度、当前主观状态、阶段信号与提醒组成完整模型输入，并通过感知提供方的持久游标追加新材料。所有已输入消息留在同一 Thread；感知提供方负责自身增量位置的语义。

## 一、使用

构造 `CognitiveInput(threads, perception, consumer='operating_context', environment='', precision=None, reminders=None)`，作为 LLMDriver 的 input_builder。默认消费 ActorStore 返回的 ActorRecord：persona 保留整个原值，可包含类型背景和实例背景；state 为本次实际激活取得的完整主观状态。environment、precision、reminders 可以是原值或同步、异步 session 回调。

感知回调 `perception(session, position)` 返回 `(messages, next_position)`。messages 为本轮需要追加的完整消息列表，首轮 position 为 None。后续位置由提供方解释，例如消息序号或组合水位；框架不扫描历史推算新增内容，也不裁剪提供方返回的原文。当前状态和本轮提醒在每次实际激活加入；persona、环境说明和精度在此 Thread 首次消费时加入，此后每次重新求当前材料，与已有输入事件引用的原文比较；变更时追加新原文，未变时保留此前消息。

## 二、保存

`InputBatch(messages, consumer, cursor, context=None)` 是显式输入结果。LLMDriver 调用 ThreadStore.append_input，在同一短事务内追加所有消息并推进该 Thread、consumer 的游标；任一消息编码失败会回滚两者。游标正文使用与 Thread 相同的分块正文存储，索引保留对应事件序号及最近上下文消息的引用，不在每轮 cursor 复制完整背景。普通返回消息列表的 input_builder 仍可作为没有持久增量位置的轻量构建方式。

同一主体、同一 Moment、同 mode 使用持久 Thread 定位，因此阶段 A→B→A 与独立进程恢复都能读回原消费位置。Runtime 的临时 cursors 字典仅负责当前激活引用，不承担感知位置恢复。CognitiveInput 的位置封套区分尚未初始化与提供方合法返回 None，避免把空位置误当作首次会话。

## 三、边界

动态信息的可得范围仍由绑定主体的 Information 与感知提供方决定。此构建器没有创建跨网络请求的数据库读事务，返回材料也不宣称对应任意历史 World 快照。provider 可返回实际 revision 供主体理解信息时间。

确定性测试验证完整背景和大原文进入实际提供方输入、阶段往返继续位置、独立进程恢复继续位置，以及消息失败不推进消费位置。模型 profile 的主体、类型与单次覆盖选择由提供方选择接入单独验收；这些测试不替代真实模型认知质量评估。
