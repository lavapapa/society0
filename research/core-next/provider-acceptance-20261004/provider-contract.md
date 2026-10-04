# SiliconFlow 实际接口核查

本记录对应用户在 2026-10-04 明确指定的提供方与模型。密钥由进程环境注入，未保存到本目录。模型清单查询确认 `Qwen/Qwen3.8-27B` 及 Qwen3 Embedding 三种规模可用；清单可见性与实际调用分别判断。

## 一、实际配置

地址为 `https://api.siliconflow.cn/v1`。主模型为用户指定的 `Qwen/Qwen3.8-27B`，嵌入选 `Qwen/Qwen3-Embedding-0.6B`，实际两条独立中文输入分别返回 1024 维向量。选择小型嵌入控制链路测试成本，检索与主体效果由实际对照负责。该账户和模型使用已获用户授权，不假定免费。

工具请求使用 `enable_thinking=false`、`parallel_tool_calls=false`、`tool_choice=auto`。这些配置限定本轮结构化工具测试。`endpoint-live-01/summary.json` 对应一次嵌入、一次模型自主工具调用、工具结果续轮及三次短前缀探针；完整响应保留在同目录。工具结果中的合成值被第二轮正确读取。

## 二、缓存

客户端复用一个 SDK HTTP 连接，完整消息前缀保持一致，不发送未核实的 cache 参数。约 1,379 token 的相同前缀、追加尾消息，以及 `cache-live-02/summary.json` 中两次完全相同的 8,099 token 输入，提供方均报告 `cached_tokens=0` 和 `prompt_cache_hit_tokens=0`。第二次长请求延迟下降不足以证明缓存命中。

远端推理 KV 位于服务端；本地持久消息与 HTTP 连接复用各有不同职责。已查官方 Chat API 未提供该型号明确的客户端 KV 缓存控制合同。这两组试验没有证明该型号永远不支持缓存，这两组非流式探针在相应配置下未观察到命中。随后真实多轮工具运行出现提供方来源的正缓存字段，证明实际运行存在命中；固定阈值、准确命中量与加速比仍需分别判断。完整历史继续发送；不会用回复缓存替代新决策，也不会为了缓存裁剪历史或改变主体信息。

实际流式计量另有已确认的累计口径问题：`stream-default-live-01/result.json` 保存 9 个带 usage 的 SDK chunk，末条为 1,574 输入、76 输出 token，逐条相加变成 14,166 输入、445 输出。当前安装的 Pydantic AI 默认逐 chunk 相加；显式 `openai_continuous_usage_stats=true` 时使用最新累计值。旧 `chain-live-01` 与 `round-live-02` 的 SDK 聚合值、SQL 投影和 90,112 缓存 token 总量因此不得用于费用或节省比例。SQL 投影与物理调用逐项一致，重复发生在流式 SDK 聚合层。`stream-continuous-direct-02/result.json` 已验证端点接受该设置，末条输入 1,574、输出 77 token；新运行 chain-live-03 已核对全部请求采用该设置、SDK 终态与响应及 SQL 计量一致；源完整 step=1 的一次调用实际报告 input=2225、cache read=2048。恢复分支继承该计量，不重复计作新增命中。完整依据见 `cache-runtime-evidence.md`。提供方缺失的缓存写入计数继续记为未知。

## 三、来源与后续

官方 [Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post) 定义消息、工具及非推理配置；[Embedding API](https://docs.siliconflow.cn/docs/api/embeddings-post) 列明 Qwen3 Embedding 模型和维度；[模型中心](https://www.siliconflow.cn/models) 提供模型能力说明。API 实际调用证据比通用缓存宣传更直接。

正式运行记录需保留提供方实际缓存用量，区分缺失、零及正命中；该改动的确定性覆盖由本轮 Agent 复用任务同步完成。主体循环、记忆、跨进程恢复和全部真实服务用例在产品代码冻结后执行，端点探针本身不替代这些验收。
