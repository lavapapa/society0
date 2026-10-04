# 记忆提取排队与完整历史：失败先行记录

首次运行 `uv run pytest -q tests/primary/test_kernel_memory_snapshot_acceptance.py -k queued`，先占用提供方既有 endpoint semaphore，再同时启动三项不同 Thread 的提取。失败位置为 `assert typed == []`，实际记录了三个 Thread 的 raw=True 快照。该观察确认初始 Agent.run(message_history=...) 在提供方许可前物化了全部 typed 历史。

随后将 loader 延迟到 ModelProvider.request_model 的既有许可之后，先仅修改 ThreadModel 收到的请求列表。`test_lazy_agent_history_survives_physical_retry_and_output_correction` 再次失败：第三次物理请求的首条消息为 `{'role': 'user', 'content': ''}`，预期为完整系统背景。这确认 SDK 保留的续轮列表与模型入口借到的列表存在区别，修改模型入口局部列表不足以保留完整认知。

修复使用 Pydantic AI 的公开 capture_run_messages。首次 loader 在已有端点与共享请求许可内载入规范 Thread typed 原文，并更新 SDK 实际历史列表与请求的浅引用；后续输出校验纠正消费 SDK 原历史。同一物理请求的重试复用已经载入的消息，不重复载入或追加。Thread 请求留证沿提供方原 through 与 retry_of 管理。

绿色命令为 `uv run pytest -o addopts='' -q tests/primary/test_kernel_memory_snapshot_acceptance.py -k 'lazy_agent or queued or borrows'`，完整输出保存于 cognition-queue-green.txt：3 passed, 7 deselected。另运行多个输出工具调用的纠正用例，1 passed，确认保留一次纠正与完整历史。随后认知、记忆、模型、services 与 starter 的九组组合焦点共 78 项通过；整体全量由整合工序执行。

绿色测试分别验证三任务首轮排队无 typed 物化、排队取消无 typed 物化、首次许可后仅载入一次、SDK 后续纠正轮完整原文、503 物理重试沿相同输入水位、校验纠正沿增长水位、强制工具参数与三次真实 SDK 离线 HTTP 物理请求留证。初次许可后 Agent 保留其活跃完整历史，在物理重试及逻辑纠正期间借用该对象；本实现没有声称这些已开始提取的历史会在每次网络等待时释放。
