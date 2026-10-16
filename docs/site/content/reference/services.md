# Thread 与 Memory 服务工厂

`from society0.kernel.services import thread_plugin, memory_plugin`。这两个工厂返回普通的 Plugin，把会话与经验接到同一个共享 StageStore 上，并在声明里写清需要哪些资源和退出顺序。装配时你要给出向量客户端、嵌入和提取所用的 `(插件名, 服务名)` 引用，创建向量客户端的插件负责关闭它；工厂本身不启动外部服务，也不另建一份权威存储。

## 工厂提供哪些服务

Thread 工厂提供 `threads.threads`；Memory 工厂提供 `memory.memory` 与 `memory.extension`，驱动的 extensions 引用后者。

<!-- api: society0.kernel.services.thread_plugin -->
<!-- api: society0.kernel.services.memory_plugin -->

thread_plugin 声明 Thread 的 schema，借用同一个 StageStore，没有自己的连接或后台任务。恢复时由 compose 装配已有的消息，不会重新初始化历史。

memory_plugin 的 client 是 `(插件名, 服务名)`，embedding 和 extraction 是 `(插件名, 服务名, profile名)`。embedding 指向具名的 EmbeddingProvider；extraction 指向模型配置，工厂据此创建 ThreadMemoryExtractor。研究里自己写的 extract 和 extraction 只能选一个。规则主体的记忆可以把 threads 传成 None，让提取器直接消费实际的结构化经历；用 Thread 做模型提取则必须有 Thread 服务。

## 装配约束与退出顺序

自动召回开启时需要有 recall_query。

自动写入能不能做，要等到知道 Thread kind、装配激活时才能核验，decision 缺提取器会失败。启用主动工具或传入 policy_selector 时，工厂会依赖 Actions 并登记动作模板；这些动作实际能不能用，由激活时的策略决定。

向量客户端由创建它的插件负责关闭。Memory 退出时先停掉自己的激活和作业，然后由主机按依赖的逆序释放嵌入、模型和向量资源。工厂不启动外部服务，也不复制额外的权威存储；等待模型、构建索引和取出正文的开销，取决于实际启用了哪些部分。

下面是已有资源上的配置片段，保留主动访问并关闭自动路径：

```python
from society0.kernel.memory import MemoryPolicy
from society0.kernel.services import thread_plugin, memory_plugin

plugins = [
    thread_plugin(),
    memory_plugin(
        client=("vectors", "client"),
        embedding=("embeddings", "embeddings", "memory"),
        policy=MemoryPolicy(auto_write=False, auto_recall=False, active_tools=True),
    ),
]
```

将这组插件与存储、交互、嵌入和向量资源一起 compose，并把 extension 绑定到驱动。更多语义见[Memory](memory.md)、[Thread](thread.md)与[组合指南](../guide/composition.md)。源码为 [memory_plugin](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/services.py#L13)。
