# Round-robin 真实失败诊断

本次从保留运行的 current.sqlite 经只读 StageReader／ThreadStore 还原全部交互，没有重发模型请求或修改产品。证据为 chain-live-01-diagnosis.json，保留真实消息、请求水位、公开工具schema和事件计数；不包含凭据字段。smoke通过，round-robin未完成，跨进程案例因首败停止尚未执行。

## 一、序列

运行仅激活主体a，Thread为7377347fe01946a9b846a0d0e50a2c70，共8个物理请求。第1轮action_find以空query成功列出3个行动，其中包括chat.send_message_to_partner；第2轮action_describe成功返回该行动的content:string参数。主体已经找到正确目标和正确行动。

第3轮action_invoke的外层arguments为对象 `{"content":"共同讨论产业预期"}`。实际提供给模型的元工具schema规定该字段为string，即调用时应传JSON编码的字符串；驱动在进入领域动作前返回 `{"error":"invalid_tool_arguments"}`。第4轮主体再次action_find(query="send")，仍成功得到同一行动。第5、6、7、8轮重复同样的对象形参，每次得到相同泛化错误。随后原8轮预算结束，状态incomplete/max_turns。

Thread计数为8个request、8个provider_response、8个tool_receipt，未出现任何领域动作start/finish事件；chat_messages为空。主体b未进入激活。不存在“已发送但没有识别终态”的证据，不能将本次解释为成功行动后多说几轮。

## 二、判断

直接原因是元工具参数的JSON双层编码要求与模型输出对象冲突。模型已正确理解业务目标、行动名、目标Ref和content原文，也读到了正确领域schema；它在外层封装上违反声明，然后持续重复。请求配置确为指定Qwen/Qwen3.8-27B、temperature=0、max_tokens=1024、parallel_tool_calls=false、enable_thinking=false。各轮实际仅调用一个工具，未见并行协议错误、截断、提供方拒绝或权限失败。

当前驱动源码在 `schemas[name].is_valid(arguments)` 失败时丢弃了字段路径和期望类型，仅返回统一错误字符串。Agent收到的反馈未说明到底是target、name还是arguments出错，也没有说明需要string。这个反馈缺口能够解释为什么重新发现行动也无助于纠正。证据支持“模型封装失配＋反馈不充分”；单次轨迹无法证明该模型普遍不能执行动作，也没有证据表明Agent适配器把原来正确的参数改坏。

## 三、下一步

最小产品判断应先让现有schema校验反馈携带实际失败字段路径与类型要求，复用校验库已有错误信息，覆盖所有元工具；保留原预算、领域语义和完整历史。若决定把动态arguments改为原生对象，应作为明确公开合同调整并核对各提供方严格schema能力，避免静默接受多种旧格式。两种决策均需由核心作者评估，本诊断未改代码。

本次工具说明已在system和元工具描述中明确JSON字符串，因此单纯重复增加说明的收益尚无证据。增加轮数只会允许当前无纠错信息的循环更久。本次未完成应继续按失败保留；完成修复与离线反例后，再由主控决定针对该例的最小真实复验。
