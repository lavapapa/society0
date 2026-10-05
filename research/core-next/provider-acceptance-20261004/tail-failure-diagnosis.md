# 多步用例的首请求中断

`tail-live-01/test_real_multi_tick_social_workflow` 的主体 a 在第一步第一个模型请求中结束为 provider_request_error。Thread 只有一个 request、一个 provider_error，无完整工具执行。物理请求 ID 为 `2107e0236fbd43dbac8c64b917e314fb`。

provider耗时 60.026645 秒，当前配置 timeout=60、max_attempts=1、trust_env=false。错误正文字符串为空。流已返回一个 action_find 的部分工具调用，arguments 为空，finish_reason 为 null；保存的 partial SDK usage 为 input=1601、output=8、cache_read=0。

这组事实高度支持外层 `asyncio.timeout(60)` 到期。当前模型留证只保存 str(error)，没有保存异常类，因此无法从产物断言具体 TimeoutError/ReadTimeout 类型。没有 HTTP 状态错误或 HTTP 错误响应证据。首请求尚未产生可执行参数，业务动作也尚未开始，本次与先前 metadata正文可达性缺口分开判断。

SQL物理调用投影为 requests=1、errors=1、responses=0，用量报告缺失；partial用量保留作诊断，未冒充最终响应或费用。用例退出耗时 61.26 秒包含外层清理。VFS 尚未执行，不记为通过或失败。
