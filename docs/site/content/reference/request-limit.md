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

## 三类额度与激活槽

请求许可、可选总量许可和激活槽分别控制不同的东西，配置时不要互相替代。

<!-- api: society0.kernel.activation_slots.ActivationSlotPolicy -->
<!-- api: society0.kernel.activation_slots.ActivationSlots -->
<!-- api: society0.kernel.activation_slots.ActivationWorkload -->
<!-- api: society0.activation_pool.ActivationPool -->

`model_plugin` 与 `embedding_plugin` 各接受一个物理请求许可；模型与嵌入是两个独立对象，默认不共享。可选的总量许可是第三种对象，只在确有跨服务总上限时传入 `total_limit`，它按物理请求计，不替代端点并发或服务许可。不传时沿用原来的行为：各端点容量与服务许可照旧生效。

激活槽限制一次完整激活占用的端点容量，物理请求许可限制正在发送的请求。`ActivationSlotPolicy` 给出每路由额度、按路由覆盖和可选总量；`ActivationSlots` 持有这些信号量，`lease(route)` 在激活存续期间保留槽位，其路由许可与总量许可在同一个取消安全区获取和归还。`snapshot(pool)` 在接入工作池时以池计数为准，避免两处计数给出不同答案。`ActivationWorkload` 按 Thread 已持久化的路由给出 `(工作负载, 容量, 路由)`，同一 Moment、同一主体稳定；跨端点总量只约束带分组的模型任务。

`ActivationPool` 的分组让排队发生在工作者之前：同组满员的任务留在邮箱，另一组可以继续入场，因此一个端点的等待不会占满全部 worker。分组容量与 `capacity` 是两件事，`capacity` 仍是同时执行的工作者数量。工作池入场已占的槽位以租约形式交给驱动，驱动不会重复申请同一个许可。

## 何时申请、何时释放

取得端点的许可之后，物理请求在必须留证之前申请共享许可，SDK 返回或者抛错之后释放。排队期间被取消，不会登记成已经发出的物理尝试；留证失败会释放许可并把异常向上传播。重试退避、缓存命中、微合批排队都不占用共享许可。一次嵌入物理批次只申请一次，多个逻辑消费者各自保留自己的归属。

模型请求先固定 Thread 水位，取得额度之后才加载这个水位的完整历史。重试退避之前释放本次的消息列表，然后按同一个水位重建；完整历史和顺序都保留。关闭提供方会收束它自己的等待者和任务，共享给其他服务的额度仍然由原对象管理。

## 成本边界

许可控制实际并发的请求数量，以及获准加载的上下文有多少。

单个请求仍然要为完整历史付出内存下界，SDK、返回值和调用方保留的对象还有额外的驻留开销；嵌入缓存、Chroma 索引和存储空间各自有独立的预算。

这个额度不换算 token 或费用，也不调整主体的轮次、行动预算和业务阶段的顺序。排队时间计入 queue_s，SDK 等待计入 provider_s；各段时长的包含关系见[诊断](diagnostics.md)。业务独立性与主体并发见[并行指南](../guide/parallel.md)。实际获准读取与重试的入口为 [ModelProvider.request_model](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/models.py#L362)。
