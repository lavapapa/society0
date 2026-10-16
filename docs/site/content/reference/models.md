# 模型与嵌入提供方

`society0.kernel.models` 提供模型、嵌入及其 Plugin 工厂；`society0.kernel.model_selection.ModelResolver` 在已安装的具名配置间选择。凭据、地址和实际模型名由运行配置注入。

## 一、模型接口

每个 profile 管理自己的端点与连接。工厂提供 `models[profile]`，并把关闭注册到主机生命周期。

<!-- api: society0.kernel.models.model_plugin -->
<!-- api: society0.kernel.models.ModelProvider -->
<!-- api: society0.kernel.models.ModelProvider.request_model -->
<!-- api: society0.kernel.model_selection.ModelResolver -->
<!-- api: society0.kernel.model_selection.ModelResolver.resolve -->

`request_model` 返回原始 SDK ModelResponse、已保存消息序号和未完成原因；`request` 返回 assistant 角色视图及 finish_reason。每次调用选项覆盖 profile 的同名顶层值，嵌套对象整体替换。端点按正权重选择，同一 profile 使用同一模型。

ModelResolver 的选择顺序为本次 override、主体配置、类型配置、默认配置。构造时未知静态引用抛 ValueError；未知 override 在查表时抛 KeyError。选择复用已有连接池，同 Moment 更换提供方仍沿用完整 Thread。

物理请求先取得许可，再加载固定水位的完整消息并留证。SDK 重试关闭，适配器按 max_attempts 重试可重试的连接、超时、限流和服务端错误；每次尝试有独立身份。授权等非重试故障成为 ProviderFailure，正文留证失败直接传播。完整历史加载、传输和响应保存均有实际成本。

## 二、认证与会话

普通 API 端点通过显式 api_key 等配置接入。Codex 订阅有两条认证入口：`provider_type="openai-codex"` 读取 Codex CLI 凭据，CODEX_HOME 可指定其目录；`provider_type="siwc"` 使用 Society0 应用独立授权的具名 account。两者使用 Responses 通路，原生 Codex 不提供 embedding，且无付费 API 回退。

`session_transport=None` 将会话身份保存在 Thread。支持 metadata 的普通提供方可显式选用 metadata，把稳定 session_id 放入请求正文。订阅提供方拒绝此选项。服务的参数支持、吞吐和价格由实际端点决定，通用配置不固定远端型号。

## 三、嵌入与资源

嵌入服务按原输入位置返回向量，Memory 与 Social 可共享此入口。

<!-- api: society0.kernel.models.embedding_plugin -->
<!-- api: society0.kernel.models.EmbeddingProvider -->
<!-- api: society0.kernel.models.EmbeddingProvider.embed -->
<!-- api: society0.kernel.models.ResourceCalls.read -->

embed 的 metadata 保存主体、用途及可选 thread_id；带 Thread 时核验主体归属。微批和缓存共享相同原文的向量，返回时展开至每个输入位置。超出批次字符预算的单段原文完整单独发送。响应条数或维度不符时保留原响应并拒绝缓存。

ResourceCalls 保存物理批次和逻辑使用的来源关联；`read` 物化完整正文。一个逻辑等待者取消后，其他主体的共享请求仍继续。关闭先拒绝新操作，取消并排空所属调用，再释放客户端。缓存受各 profile 的条数与字节预算约束；共享请求许可单独控制物理并发。

以下片段选择已有配置，无外部调用：

```python
from society0.kernel.model_selection import ModelResolver

def select_provider(profiles):
    resolver = ModelResolver(profiles, default="decision",
                             actor_models={"alice": "analysis"})
    return resolver.resolve("alice")
```

配置步骤见[模型指南](../guide/models.md)和[订阅认证](../guide/subscription.md)，计量口径见[诊断](diagnostics.md)，并发见[请求许可](request-limit.md)。源码入口为 [ModelProvider](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/models.py#L338)。
