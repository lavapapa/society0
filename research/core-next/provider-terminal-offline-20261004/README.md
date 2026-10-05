# Responses 终态与持久消息格式验证

2026-10-04，以 provider-selection 的隔离环境运行 Pydantic AI 2.54.0 和真实 OpenAI SDK。所有 HTTP 由 MockTransport 合成，socket connect 显式拒绝，凭据是测试常量；没有调用模型或访问账户。原探针与其证据未修改。

## 一、结果

每个流先给出同样的完整-looking function call，再分别结束。response.incomplete（max_output_tokens）返回 finish_reason=length，仍包含一个工具；response.failed 和流内 error 抛 ModelAPIError；直接 EOF 返回 finish_reason=None，仍包含一个工具；response.completed 返回 finish_reason=stop，provider_details.finish_reason=completed。

因此公共 API 足够支持保守终态判断，但正常返回本身不代表模型完整结束。Society0 在该 Responses profile 应等请求消费完，再核对明确成功终态后执行工具。length、None、流异常分别记录未完成；已收到的正文保存为诊断。这个检查属于应用的动作合同，不需要再写 SSE 解析器。其他提供方的终态映射另按对应公开 API 核实，不能机械复用本次 stop/completed 字符串。

本次没有模拟所有网络断开类型，也没有测试实际提供方服务器。合成错误验证 SDK 的映射与应用可观察边界，不构成真实服务验收。业务 terminal action 的含义仍由行动政策决定，与 response.completed 的传输完成不同。

## 二、消息

成功响应经公开 ModelMessagesTypeAdapter.dump_json/validate_json 编解码后，连同工具回执再次发给真实 SDK。捕获的请求仍含 encrypted_content 签名、society namespace 与原 arguments 字符串。结果覆盖 SDK 已识别的 opaque reasoning 字段，未宣称任意未来未知字段无损。

复现命令为 `/tmp/society0-provider-selection-20261004/venv/bin/python research/core-next/provider-terminal-offline-20261004/probe.py`，输出见 result.json。环境安装版本沿相邻 provider-selection 报告；脚本不导入 Society0 产品，不执行领域动作。

结论是继续采用公开 Model API，并保留极小、显式的响应终态闸门；模型输出流到达工具片段时立即执行的路径不满足本项目完整步骤合同。
