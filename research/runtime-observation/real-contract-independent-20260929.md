# 真实行动测试合同的独立复查

本次复查由 threads 执行，测试修改由 spec 负责。基线真实测试的失败暴露了旧断言将预算耗尽当作成功的问题，因此先核对任务语义，再决定是否迁移测试合同。

## 一、判断

五项测试分别要求发送一次伙伴消息、发布一次帖子、查询一次热帖，以及两个社会交互流程中的首次发帖。指令均写明指定动作完成后结束，没有要求工具调用后继续分析、提交结构化答案或完成其他动作。将对应动作名称作为 completion_action_tags，符合这些测试的可验收业务目标；框架已自动把动作名登记为标签，返回终止原因为 completion_action_tag。

改动保持原有 max_turns、max_action_calls、action_call_limits、模型参数、业务事实、动作次数、嵌入与记忆断言。与新合同矛盾的 action_budget_exhausted 成功预期改为 completion_action_tag；social_publish 的至少两次模型调用假设改为每主体至少一次，保留真实 instruct 跟踪和成功动作检查。这项次数变化移除了过时的额外收尾调用假设，未改变工具执行目标。

## 二、边界

独立复验新增 test_nonterminal_budget_exhaustion_never_becomes_success_memory：无终止标签、总动作预算一，动作实际成功一次、模型请求一次，最终仍为 error/incomplete/action_budget_exhausted；记忆召回可执行一次，所有记忆写入 spy 均为空，也没有 memory_extract 请求。它与既有预算耗尽禁止补发收尾、禁止重试已完成动作和终止标签正例合计九项通过，日志为 real-contract-independent-20260929.txt。

真实十四项用于提供方、嵌入、记忆、恢复与生命周期验收，并报告本轮绝对阶段时间。基线失败与新版成功对应不同完成状态，不能用其总耗时计算整体提速。性能改善由相同成功语义的二十步本地 Agent 对照、相同数据的存储和查询以及共享文件系统试验分别证明。
