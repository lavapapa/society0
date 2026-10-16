# 共享物理请求许可

运行级请求额度使用标准库 `asyncio.Semaphore`。把同一个对象传给 `society0.kernel.models` 的模型与嵌入工厂，多个 profile 即可共享外部物理请求上限。

## 一、配置入口

request_limit 属于工厂和提供方参数，无需创建额外限流服务。

<!-- api: society0.kernel.models.model_plugin -->
<!-- api: society0.kernel.models.embedding_plugin -->

```python
import asyncio
from society0.kernel.models import model_plugin, embedding_plugin

def resource_plugins(model_profiles, embedding_profiles):
    request_limit = asyncio.Semaphore(8)
    return [
        model_plugin(model_profiles, request_limit=request_limit),
        embedding_plugin(embedding_profiles, request_limit=request_limit),
    ]
```

这里的 8 是本例主动选择的额度。每个运行建立自己的许可对象；未传入时沿用各 profile 和端点的容量。ModelProvider 的 global_concurrency 约束单个模型提供方，端点 concurrency 约束该端点，三者共同生效。

## 二、等待与失败

取得端点许可后，物理请求在必需留证前申请共享许可，SDK 返回或抛错后释放。排队期间取消不登记已发物理尝试；留证失败释放许可并向上传播。重试退避、缓存命中、微合批排队均不占共享许可。一次嵌入物理批次申请一次，多个逻辑消费者保留各自归属。

模型请求先固定 Thread 水位，在取得额度后加载该水位的完整历史。重试退避前释放本次消息列表，再按同一水位重建；完整历史和顺序保留。关闭提供方收束自身等待者与任务，共享给其他服务的额度仍由原对象管理。

## 三、成本边界

许可控制实际并发请求及获准加载的上下文数量。单个请求仍有完整历史的内存下界，SDK、返回值和调用方保留对象另有驻留成本；嵌入缓存、Chroma 索引和存储空间有独立预算。

该额度不换算 token 或费用，也不调整主体轮次、行动预算、业务阶段顺序。排队计入 queue_s，SDK 等待计入 provider_s；各时长的包含关系见[诊断](diagnostics.md)。业务独立性与主体并发见[并行指南](../guide/parallel.md)。实际获准读取与重试入口为 [ModelProvider.request_model](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/models.py#L362)。
