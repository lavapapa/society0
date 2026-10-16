# 共享物理请求许可

运行级请求额度就是标准库的 `asyncio.Semaphore`。把同一个信号量对象传给 `society0.kernel.models` 里的模型工厂与嵌入工厂，多个 profile 就共用一个外部物理请求上限；对象由调用者创建和持有，传给哪些工厂也由调用者决定。

## 配置入口

request_limit 是工厂和提供方的参数，不需要再创建一个限流服务。

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

代码里的 8 是这个例子自己选的额度。每一次运行都建立自己的许可对象；不传 request_limit 时，沿用各个 profile 和端点的容量。ModelProvider 的 global_concurrency 约束单个模型提供方，端点的 concurrency 约束该端点，三者一起生效。

## 何时申请、何时释放

取得端点的许可之后，物理请求在必须留证之前申请共享许可，SDK 返回或者抛错之后释放。排队期间被取消，不会登记成已经发出的物理尝试；留证失败会释放许可并把异常向上传播。重试退避、缓存命中、微合批排队都不占用共享许可。一次嵌入物理批次只申请一次，多个逻辑消费者各自保留自己的归属。

模型请求先固定 Thread 水位，取得额度之后才加载这个水位的完整历史。重试退避之前释放本次的消息列表，然后按同一个水位重建；完整历史和顺序都保留。关闭提供方会收束它自己的等待者和任务，共享给其他服务的额度仍然由原对象管理。

## 成本边界

许可控制实际并发的请求数量，以及获准加载的上下文有多少。

单个请求仍然要为完整历史付出内存下界，SDK、返回值和调用方保留的对象还有额外的驻留开销；嵌入缓存、Chroma 索引和存储空间各自有独立的预算。

这个额度不换算 token 或费用，也不调整主体的轮次、行动预算和业务阶段的顺序。排队时间计入 queue_s，SDK 等待计入 provider_s；各段时长的包含关系见[诊断](diagnostics.md)。业务独立性与主体并发见[并行指南](../guide/parallel.md)。实际获准读取与重试的入口为 [ModelProvider.request_model](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/models.py#L362)。
