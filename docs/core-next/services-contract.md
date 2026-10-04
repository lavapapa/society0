# 基础服务装配

Thread 和记忆通过普通 Plugin 装入同一个运行主机。工厂位于 `society0.kernel.services`；导入工厂模块保持轻量，选择某项服务时才载入其实现。标准模型、嵌入、交互与运行插件继续使用各自已有的服务合同。

## 一、Thread

`thread_plugin(storage=('storage', 'store'), name='threads')` 声明 Thread 的权威表，提供 `threads.threads`。它借用主机中的 StageStore，随主机存储作用域结束；该服务本身不另开连接或后台任务。新建与完整点恢复均由 compose 处理，恢复时不会再次初始化消息。

## 二、记忆

`memory_plugin(client=..., embedding=..., extraction=..., policy=..., recall_query=...)` 提供 `memory.memory` 与 `memory.extension`。驱动的 extensions 引用后者，Memory 生命周期由认知扩展管理。client 是 `(插件名, 服务名)`；embedding 和 extraction 是 `(插件名, 服务名, 配置名)`，分别选择标准 embedding_plugin 和 model_plugin 提供的配置。也可使用 extract 提供研究定义的提取策略，与 extraction 二选一。extraction 可以省略，decision 激活选择自动写入时须提供提取策略，检查发生于已知 Thread kind 的激活装配；自动召回开启时须提供 recall_query。主动工具、自动召回和自动写入沿 MemoryPolicy 独立配置。

默认 storage、threads、actions 服务分别是 `storage.store`、`threads.threads`、`interaction.actions`。主动工具开启时，工厂向交互服务注册记忆动作；未设置策略选择器且默认关闭时该依赖随之省去。设置 policy_selector 后保留常驻模板，并按激活策略控制实际可用性。向量 client 由所声明的依赖插件提供并管理关闭，主机依赖顺序保证 Memory 收束后再释放嵌入、模型及向量客户端。客户端可由运行配置选择实际持久目录，工厂不会自行启动外部服务。

没有自动提取模型的组合可显式关闭 auto_write，继续使用种子导入、原文读取、召回和主动工具。完整步骤内保存的记忆正文、向量和作业随 StageStore 恢复；派生向量索引沿 Memory 既有重建与可见性合同使用。

规则记忆可传 threads=None，并由驱动将实际结构化经历写入 ActivationContext.experience，提取策略消费该经历；普通无记忆规则主体无需 Thread、向量或模型依赖。

这些服务工厂提供正式装配路径。运行入口负责配置选择与资源拥有关系，完整消息、模型调用和记忆事实仍由各自规范写入器保存。
