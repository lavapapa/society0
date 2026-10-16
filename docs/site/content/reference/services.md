# Thread 与 Memory 服务工厂

`from society0.kernel.services import thread_plugin, memory_plugin`。这两个工厂返回普通 Plugin，把会话与经验接入共享 StageStore，并声明所需资源和退出顺序。工厂模块保持轻量，选择能力时才导入对应实现。

## 一、接口与服务

Thread 工厂提供 `threads.threads`；Memory 工厂提供 `memory.memory` 与 `memory.extension`，驱动的 extensions 引用后者。

<!-- api: society0.kernel.services.thread_plugin -->
<!-- api: society0.kernel.services.memory_plugin -->

thread_plugin 声明 Thread schema，借用同一个 StageStore，无独立连接或后台任务。恢复通过 compose 装配既有消息，不重新初始化历史。

memory_plugin 的 client 是 `(插件名, 服务名)`，embedding 和 extraction 是 `(插件名, 服务名, profile名)`。embedding 指向具名 EmbeddingProvider；extraction 指向模型配置，工厂据此创建 ThreadMemoryExtractor。研究自定义 extract 与 extraction 二选一。规则记忆可选 threads=None，用实际结构化经历供提取器消费；Thread 模型提取要求 Thread 服务。

## 二、约束与生命周期

自动召回开启时需要 recall_query。自动写入是否可行在已知 Thread kind 的激活装配时核验，decision 缺少提取器会失败。主动工具开启或存在 policy_selector 时，工厂依赖 Actions 并登记动作模板；激活策略决定实际可用性。

创建向量客户端的插件负责关闭客户端。Memory 退出先收束自己的激活与作业，再由主机逆依赖释放嵌入、模型和向量资源。工厂不启动外部服务，不复制额外权威存储；模型等待、索引构建和正文物化成本来自实际启用的消费者。

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
