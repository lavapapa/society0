# 模型与嵌入提供方

`society0.kernel.models` 提供模型、嵌入以及它们的 Plugin 工厂，`society0.kernel.model_selection.ModelResolver` 在已经安装的具名配置之间做选择。端点的凭据、地址和实际模型名由你的运行配置注入。每个 profile 管理自己的端点与连接，工厂通过 `models[profile]` 提供对应的提供方，并把关闭动作登记到主机生命周期，连接池不需要你手动关闭。

## 模型请求与 profile 选择

模型请求、profile 选择和重试规则都由这些接口决定。

<!-- api: society0.kernel.models.model_plugin -->
<!-- api: society0.kernel.models.ModelProvider -->
<!-- api: society0.kernel.models.ModelProvider.request_model -->
<!-- api: society0.kernel.model_selection.ModelResolver -->
<!-- api: society0.kernel.model_selection.ModelResolver.resolve -->

`request_model` 返回原始的 SDK ModelResponse、已保存消息的序号，以及未完成时的原因；`request` 返回 assistant 角色视图和 finish_reason。每次调用的选项会覆盖 profile 里的同名顶层值，嵌套对象整体替换。端点按正权重选择，同一个 profile 使用同一个模型。

ModelResolver 依次看本次 override、主体配置、类型配置，最后才是默认配置。构造时出现未知的静态引用会抛 ValueError；未知的 override 要等到查表时才抛 KeyError。选择出来的提供方复用已有的连接池，在同一个 Moment 里换提供方也仍然沿用完整 Thread。

物理请求先取得许可，然后按固定的水位把完整消息加载出来并留证。SDK 自带的重试关闭，适配器按 max_attempts 重试可以重试的连接错误、超时、限流和服务端错误；每一次尝试都有独立的身份。授权这类不可重试的故障变成 ProviderFailure，正文留证失败则直接传播。加载完整历史、传输和保存响应都有实际开销。

## 认证方式与会话身份

普通 API 端点用显式的 api_key 等配置接入。Codex 订阅有两条认证入口：`provider_type="openai-codex"` 读取 Codex CLI 的凭据，CODEX_HOME 可以指定它的目录；`provider_type="siwc"` 使用 Society0 应用单独授权的具名 account。两者都走 Responses 通路，原生 Codex 不提供 embedding，也没有付费 API 回退。

`session_transport=None` 时会话身份保存在 Thread 里。支持 metadata 的普通提供方可以显式开启 metadata，把稳定的 session_id 放进请求正文；订阅提供方拒绝这个选项。服务支持哪些参数、吞吐和价格如何，都由实际端点决定，通用配置里不写死远端型号。

## 嵌入、共享调用与资源开销

嵌入服务按每个原文输入的位置返回向量，Memory 和 Social 可以共用这个入口。

<!-- api: society0.kernel.models.embedding_plugin -->
<!-- api: society0.kernel.models.EmbeddingProvider -->
<!-- api: society0.kernel.models.EmbeddingProvider.embed -->
<!-- api: society0.kernel.models.ResourceCalls.read -->

embed 的 metadata 保存主体、用途和可选的 thread_id；带 Thread 时会核验主体归属。微批和缓存让相同原文共用一份向量，返回时再展开到每个输入位置。单段原文超出批次的字符预算时，整段单独发送。响应的条数或维度不对时，原始响应保留下来，同时拒绝写入缓存。

ResourceCalls 保存物理批次与逻辑使用之间的来源关联，`read` 会把完整正文取出来。某个逻辑等待者取消之后，其他主体共享的请求仍会继续。关闭时先拒绝新操作，取消并排空自己负责的调用，再释放客户端。缓存受每个 profile 的条数和字节预算限制；共享请求许可单独控制物理并发。

以下片段选择已有配置，无外部调用：

```python
from society0.kernel.model_selection import ModelResolver

def select_provider(profiles):
    resolver = ModelResolver(profiles, default="decision",
                             actor_models={"alice": "analysis"})
    return resolver.resolve("alice")
```

配置步骤见[模型指南](../guide/models.md)和[订阅认证](../guide/subscription.md)，计量口径见[诊断](diagnostics.md)，并发见[请求许可](request-limit.md)。源码入口为 [ModelProvider](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/models.py#L338)。
