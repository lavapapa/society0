# 为不同工作选择并发方式

要减少等待时，先跑一次短运行，看清阶段计时、模型请求和持久化各花多少，再决定并发哪一类工作：主体激活、外部请求和 CPU 计算各有自己的容量和顺序边界。下面从[自己的运行计划](../tutorial/first-plan.md)开始；用进程图示例时还要备好图依赖和输入文件。

## 让互不干扰的主体一起行动

默认的 serial 阶段保住行动顺序，后面的主体能读到之前提交的状态。只有研究明确要求一批主体各自独立工作时，才用 independent。下面是已有阶段函数的装配片段：

```python
from society0.kernel.runtime import Phase

phase = Phase("independent_measurement", measure,
              execution="independent", capacity=4)
```

`measure` 是研究计划里的阶段函数，按[调度指南](schedule.md)激活主体。这里的 4 只是示例容量，运行前要换成研究和资源允许的值。共同库存、余额或配额有争用时，就把串行次序写明确；声明成并发并不能证明业务上真的互不干扰。

核对运行清单里的有效容量和主体结果数量，再拿串行基线对照事实是否符合预期。有多少主体在等网络、有多少请求真正在发，这两个数量分别由不同的层次控制。

## 限制真正发出的请求数

模型端点的 concurrency 控制单个端点的许可，profile 还能用 global_concurrency 控制总许可。多个模型或嵌入 profile 共用同一份服务容量时，给它们传同一个 `asyncio.Semaphore`。下面是运行装配里的片段，两个 profiles 已经按[模型指南](models.md)定义：

```python
import asyncio
from society0.kernel.models import model_plugin, embedding_plugin

request_limit = asyncio.Semaphore(4)
models = model_plugin(model_profiles, request_limit=request_limit)
embeddings = embedding_plugin(embedding_profiles, request_limit=request_limit)
```

共享许可管的是实际发出的物理请求，缓存命中、等待合批和重试退避都不占这份许可。请求上限、HTTP 池大小和主体阶段容量要分别配置，调大其中一个不一定能让总等待变短。用实际物理尝试的计时看排队和提供方各花多久，再按已知的服务额度调整。

## 把纯计算放进进程池

进程池接收可以序列化的输入，返回派生出来的结果。共享数据库、Runtime 和主体状态都留在父进程。下面是异步作用域里的装配片段，输入要事先准备成[图示例](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/graph_environment.py)那样的格式：

```python
from society0 import compose
from society0.plugins import compute_plugin
from examples.core_next.graph_environment import graph_plugin

async def compute_graph(path, source):
    async with compose(path, [
        compute_plugin(max_workers=2, max_pending=4),
        graph_plugin(source, compute=("compute", "compute")),
    ]) as host:
        graph = host.service("graph", "graph")
        return await graph.projection_async()
```

把调用放进 `if __name__ == "__main__":` 保护的入口；完整执行方式见[进程图例子](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/parallel_graph.py)。父进程在一个短读事务里取节点、边和版本，释放视图之后才等计算，结果回来时核对来源有没有变。

自己写的计算函数放在模块顶层，输入里只放本批需要的值。不同批次各自提交，父进程按事先定好的次序合并，再通过规范写入器登记结果。`max_pending` 限制的是任务数量，输入字节数和拼批次占的内存仍然由调用者自己控制。

## 对照成本，看清失败边界

用同一份输入，对照串行和并行得到的完整结果、次序和数值口径，并分别记录父进程内存、计算时间、网络等待和持久化耗时。单个很大的任务只会用到其中一个 worker，拆得太碎的任务又可能被序列化和进程调度的开销盖过。

进程里的任务异常会沿着 compute.run 传播出来；Runtime 按该阶段的设置保留失败。从可信的完整步骤重启时，先看哪些事实已经写入，别把领域动作重复做一遍。关闭主机要等它拥有的任务收束，长时间计算可能让退出拖得更久。精确生命周期见[计算参考](../reference/compute.md)与[请求许可参考](../reference/request-limit.md)，完整步骤的处理见[存储指南](storage.md)。
