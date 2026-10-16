# 为不同工作选择并发方式

先查看一次短运行的阶段计时、模型请求和持久化成本，再决定要并发哪一类工作。主体激活、外部请求和 CPU 计算各有自己的容量与顺序边界。本页需要已有[运行计划](../tutorial/first-plan.md)；使用进程图示例时还需其图依赖与输入文件。

## 一、并发独立主体

默认 serial 阶段保留行动顺序，后续主体可以读取此前提交的状态。若研究明确要求一批主体独立工作，才使用 independent。以下为已有阶段函数的接线片段：

```python
from society0.kernel.runtime import Phase

phase = Phase("independent_measurement", measure,
              execution="independent", capacity=4)
```

`measure` 是研究计划的阶段函数，按[调度指南](schedule.md)激活主体。这里的 4 是示例容量，运行前替换为研究和资源合同允许的值。共同库存、余额或配额存在争用时保持明确串行次序；并发声明本身不会证明业务独立。

核对运行清单的有效容量与主体结果数量，检查与串行基线的事实是否满足预期对照。等待网络的主体数量和正在发送的请求数量分别由两个层次控制。

## 二、限制实际请求

模型端点的 concurrency 控制单端点许可，profile 可再用 global_concurrency 控制总许可。多个模型或嵌入 profile 共用服务容量时，可向它们传同一个 `asyncio.Semaphore`。以下为运行装配中的接线片段，两个 profiles 已按[模型指南](models.md)定义：

```python
import asyncio
from society0.kernel.models import model_plugin, embedding_plugin

request_limit = asyncio.Semaphore(4)
models = model_plugin(model_profiles, request_limit=request_limit)
embeddings = embedding_plugin(embedding_profiles, request_limit=request_limit)
```

共享许可约束实际物理请求，缓存命中、合批等待和重试退避不占该许可。请求上限、HTTP 池大小和主体阶段容量分别配置，增加其中一个未必减少总等待。用实际物理尝试计时检查排队和提供方耗时，再按服务已知额度调整。

## 三、把纯计算交给进程池

进程池接收可序列化输入，返回派生结果。共享数据库、Runtime 与主体状态留在父进程。下面是异步作用域中的装配片段，需要事先准备符合[图示例](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/graph_environment.py)格式的输入：

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

把调用放在 `if __name__ == "__main__":` 保护的入口；完整执行方式见[进程图例子](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/parallel_graph.py)。父进程在短读事务内取得节点、边和版本，释放视图后等待计算，返回时核对来源未变化。

自定义计算函数放在模块顶层，输入只包含本批所需值。不同批次独立提交，父进程按预定次序合并，并通过规范写入器登记结果。max_pending 限制任务数量，输入字节和构造批次的内存仍由调用者控制。

## 四、验证成本与失败边界

使用同一输入，对照串行与并行的完整结果、次序和数值口径；分别记录父进程内存、计算时间、网络等待和持久化耗时。一次大任务只使用一个 worker，细碎任务又可能被序列化与进程调度成本主导。

进程任务异常沿 compute.run 传播；Runtime 按阶段合同保留失败。从可信完整点重启时检查哪些事实已写入，避免重复领域动作。关闭主机需要等待拥有的任务收束，长计算可能延长退出时间。精确生命周期见[计算参考](../reference/compute.md)与[请求许可参考](../reference/request-limit.md)，完整步骤处理见[存储指南](storage.md)。
