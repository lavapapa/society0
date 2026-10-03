# 2026-10-04 提供方初探

本轮读取已授权提供方凭据仅用于进程内请求。硅基流动对应凭据的 models 请求返回 401。OpenRouter 凭据验证返回 200，官方目录列出了 Qwen3.8-Flash 与 Qwen3-Embedding-8B；实际付费请求全部返回 402，消息明确为账户尚未购买额度。未追加重试、未购买额度、未抬高请求预算。

Qwen3.8-Flash 官方目录价格为输入每百万 token 0.15 美元、输出 0.47 美元；Qwen3-Embedding-8B 为输入每百万 token 0.01 美元。目录及参数声明保存在 provider-models-20261004.json 与 provider-embedding-models-20261004.json。来源为 [模型目录](https://openrouter.ai/api/v1/models)、[Qwen3.8-Flash](https://openrouter.ai/qwen/qwen3.8-flash)、[嵌入接口](https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings) 与 [嵌入目录](https://openrouter.ai/api/v1/embeddings/models)。这些价格是查询时点事实，并非长期承诺。

实际 schema 与 tool 请求使用 max_tokens=256、temperature=0、parallel_tool_calls=false、reasoning.enabled=false，router fallback=false，客户端 max_attempts=1。两个请求分别耗时约 1.289 秒与 0.395 秒，均返回余额失败；双原文、512 维嵌入请求约 1.788 秒返回相同余额失败。原始结果与 Thread 保存在本机 /tmp/society0-core-next-provider-evidence-20261004/provider-real-20261004。本次费用受理失败，不能据此宣称参数支持、调用成功或延迟基准。

待后续确认可用端点，再使用新运行目录验证相同小合同。本次失败记录保留。完整 Memory 物理合批留证与恢复验收仍须独立完成。

## 既有服务探针

授权内网端点 http://10.244.93.52:4000/v1 的 models 返回 200，列出 qwen3.8-27b 与 bge-large-zh-v1.5。qwen3.8 的 schema、tool 两次请求均达到配置超时，未继续重复同一 alias。schema 总墙钟 136.618 秒包含从旧 Ceph venv 冷读依赖的停顿；tool 为 60.065 秒。这些数值不可作为纯服务推理延迟。双文本 embedding 使用 omit dimensions 合同，返回 200、每段 1024 维，HTTP 墙钟 0.0578 秒；该次为端点兼容探针，尚未验证新 EmbeddingProvider 的共享留证消费者。原文保存在本机 /tmp/society0-core-next-provider-evidence-20261004/provider-existing-20261004，服务端原件仍在 /tmp/society0-core-next-20261004。

冻结部署源码保留在服务端 /tmp/society0-core-next-20261004/src，本地上传归档 /tmp/society0-core-next-probe-src.tgz；该副本含当时未提交的提供方配置改动，并非最终发行提交。依赖使用既有 Ceph venv 加本次临时目录 APSW；正式性能测量应在本地盘依赖环境进行。最终发行验收会从干净已推送提交独立部署并逐文件比较字节，本次记录用于资源定位与请求合同判断。

新适配器随后以独立部署副本 embedding-source 对 bge-large-zh-v1.5 实测：两个主体的不同原文共享一个物理请求，各得 1024 维向量，权威调用正文与两个 Thread 小引用通过完整步骤恢复。0.982 秒含适配、留证及完整步骤发布，不是纯网络指标。本机工件 /tmp/society0-core-next-provider-evidence-20261004/provider-embedding-adapter-20261004，服务端原件仍保留；外层 result.model 字段误沿用了默认 LLM 名称，实际物理 request.model 为 bge-large-zh-v1.5，已修后续脚本字段且保留原工件。该次源副本早于后续关闭与失败溯源修复，最终发行仍须统一重验。

## Gemini 工具往返

Google 官方目录提供 gemini-3.5-flash-lite；[官方价格](https://ai.google.dev/gemini-api/docs/pricing)在本次查询为输入每百万 token 0.30 美元、输出 2.50 美元。调用使用[官方 OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai)，请求 max_tokens=256、temperature=0、parallel_tool_calls=false、tool_choice=auto、reasoning_effort=minimal，单次物理尝试、30 秒超时。单工具合同成功，墙钟约 1.227 秒。

随后完成两个模型请求的工具往返：第一响应 acknowledge 工具及提供方扩展字段完整保留，工具结果进入同一 Thread 后原样送出第二请求，第二响应正常 stop，整体墙钟约 1.877 秒。此结果验证提供方扩展字段透传，不解释其不透明签名，不将它转为额外推理正文。原文分别保存在 /tmp/society0-gemini-profile-20261004 与 /tmp/society0-gemini-roundtrip-20261004。公开小结果见 provider-probe-summary-20261004.json。

该探针基于 dc6f558 之后的开发工作树，包含当时 resource_managers.py 工具扩展字段透传修订；对应回归文件 test_kernel_provider_fields.py、红绿及独立日志 provider-fields-*.txt 保留。它属于配置兼容证据，完整发布验收仍需干净已推送提交和冻结部署副本。仓库保存选定字段的小结果；SQLite、原始响应与 Thread 留在上述拥有目录，未纳入提交。
