# 提供方配置

模型服务以命名配置安装进 PluginHost，驱动选择所需配置。配置包含端点、请求默认值、并发容量与重试边界；请求和完整响应通过 Thread 留证。嵌入通过独立共享调用表保留物理请求与响应，逻辑调用引用其中的逐文本位置。

## 一、组合

`model_plugin(profiles, threads=('threads', 'threads'), name='models')` 从已安装依赖获取 ThreadStore，在 `models` 服务中提供按名字索引的 ModelProvider。每个 profile 的值是 ModelProvider 的关键字参数：endpoints、request_options、max_attempts、retry_delay、global_concurrency、http_connections、request_jitter。主机退出关闭所有提供方；安装后续 profile 失败也会清理已经注册的连接。

端点参数复用 id、api_key、base_url、model、concurrency、timeout、provider_type、api_version、deployment_name、trust_env 和 tool_choice_policy。凭据由调用方从环境或授权来源注入。端点按既有轮询选择，不把 weight 字段描述为已实现的加权调度。请求默认值与每次调用选项合并，后者覆盖同名顶层字段；extra_body 等嵌套选项按完整对象替换。

## 二、资源

每个 profile 内的全局许可和 HTTP 连接池默认容量等于该 profile 配置端点并发之和，也可分别显式指定正整数。额外随机等待默认关闭；request_jitter 为调用方主动设置的最大秒数。每个端点仍有独立并发许可。多个 trust_env 配置分别持有对应 HTTP 池，关闭由管理器负责。global_concurrency 是单个 ModelProvider 的许可；多个 profile 各自持有管理器，当前没有共享的运行总额，缓存预算也按每个嵌入 profile 计算。

SDK 重试设为零，ModelProvider 对可重试的连接、超时、限流和服务端失败执行 max_attempts。请求 timeout 保持配置端点的既有实际超时路径。余额、授权等非重试 HTTP 状态返回 provider_request_error，调用者可以显式 collect 未完成结果。Thread 留证失败和领域动作异常继续作为失败传播。

## 三、嵌入

`embedding_plugin(profiles, storage=('storage','store'), threads=('threads','threads'), name='embeddings')` 声明 RESOURCE_SCHEMA 并提供 `embeddings[profile]`。`await provider.embed(texts, metadata=...)` 返回与原输入同序的向量列表；metadata 包含 actor，LLM 调用同时给出 thread_id。纯规则主体可以选择 threads=None。Memory 与 Social 共用这一接口。

每个物理批次的请求、响应、错误写入 ResourceCalls；每个完成或提供方失败的逻辑调用保存 metadata 与每段文本的 call_id/item_index/input_index，Thread 追加小引用；失败重试尝试也保留来源。多批逻辑调用收齐已发出的结果后传播失败，部分成功向量仍可追溯。同一原文在合批和缓存中共享向量，返回时仍展开为原输入的每一个位置。缓存以模型、维度、完整文本元组标识，受条数和估算字节双重限制。取消一个等待者不取消其他主体的共享请求。

profile 可配置 dimensions、max_attempts、retry_delay、cache_max_items、cache_max_bytes、http_connections、batch_texts、batch_chars、batch_wait_ms。HTTP 默认容量等于端点配置并发之和；单个超出 batch_chars 的文本仍完整单独发送。声明维度与响应向量长度不符时保留响应原文并拒绝缓存。提供方关闭先拒绝新请求，取消并排空已有逻辑调用，再关闭底层请求资源。原生 SDK 重试关闭，适配器明确重试传输错误；权威留证失败向调用方传播，不通过拆批重发。ResourceCalls.read 是显式完整正文接口；事件保存 raw_bytes，每块保存 raw_start/raw_bytes，供观察者定位有界读取。

## 四、探针

真实端点试验脚本为 `benchmarks/core_next_provider_probe.py`，明确接收授权凭据文件路径和新输出目录。每次试验各发送一个结构化响应请求、工具请求和双文本嵌入请求，保留请求合同和失败结果；不修改业务仿真预算。各次实际模型目录、价格与结果保存在 research/core-next 的日期工件中，不作为长期固定配置。

端点可用性、参数支持及检索效果应分别验证。目录声明支持工具或结构化输出仍需成功请求确认；嵌入返回有效向量也不自动证明记忆恢复和主体归属已经贯通。
