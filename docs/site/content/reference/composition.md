# compose 与预设配方

`from society0.kernel.composition import compose` 创建或恢复共享存储，然后把机制插件装进去；`from society0.presets import rule_preset, llm_preset` 提供两种常用的单驱动组合。调用者要给出新的运行目录和插件序列，从已有运行恢复时再指明源运行与完整步骤。进入 `compose` 之后得到就绪的 PluginHost，退出时的关闭动作由它统一执行。

## 装配、恢复与退出

装配按什么顺序进行、恢复跳过哪些步骤、退出时关掉什么，都写在 `compose` 这一层。

<!-- api: society0.kernel.composition.compose -->

```python
async with compose(new_run, plugins, source=old_run, step=10) as host:
    runtime = host.service('runtime', 'runtime')
```

`new_run` 必须是新的目标目录。省略 source 时创建一个新的根；给出 source 就从该运行的完整步骤恢复，`step` 省略时取源里最新的完整步骤。只给 step 而没有 source 会抛 `ValueError`。`storage` 是 compose 自己注入的保留实例名，指向同一个 StageStore。

装配先展开 includes、检查服务与数据两张依赖图，再按数据图汇集 DDL，进入 prepare 资源，并在同一个根事务里运行初始化。一个插件同时提供 initialize 和 prepare 时，先运行普通的初始化，再运行 prepare 产出的同步函数。初始化返回 awaitable 会抛 `TypeError`。准备阶段进入的资源在安装服务之前按逆序释放，随后按服务图安装服务。

恢复时先编译插件声明的 DDL，把它和源运行的 schema 比较；对不上就在还没有复制源业务数据的时候停下。恢复跳过 initialize 和 prepare，安装函数要根据恢复出来的状态重建连接、索引或派生缓存。初始化失败不会发布目标；如果根已经发布成功，之后关闭准备资源或安装服务再失败，这个有效的根会保留下来，同时释放已经占用的资源。

退出时依次停掉任务、关闭主机服务和存储，正在执行的步骤不会被自动完成。创建新根和恢复都会复制数据库；运行中的日常发布由 Runtime 负责。

## 两种预设配方

预设返回普通的 Plugin 元组，可以直接交给 RunPlan；它们本身不启动运行。

<!-- api: society0.presets.rule_preset -->
<!-- api: society0.presets.llm_preset -->

`rule_preset` 把交互、结果、规则驱动、主体映射、Runtime 和调用者传入的 schedule 组合在一起；`run` 回调应当返回 DriverResult，`allows` 决定资源权限。`llm_preset` 在此基础上加入 Thread，并要求显式给出 provider 与 input_builder 的服务引用；模型和认知来源通过 plugins 加入。

两种预设都要求你在 `records` 和外部 `data=(插件, 服务)` 之间选一种，两个都提供会抛 `ValueError`。规则主体的持久驱动标签是 `rule`，模型主体是 `llm`。用外部目录时，初始记录由那个目录写入。extensions 会传给驱动扩展；开销大的共享资源交给对应的插件持有。

默认预设里已经包含几个具名的基础服务，自己再加 plugins 时不要重复声明这些实例。`runtime=` 用来整体替换 Runtime 插件，需要明确设置容量、预算或其他服务引用时使用。预设不会替你生成领域权限、时间线、模型端点或记忆策略；多驱动、多实例的组合直接声明 Plugin 元组即可。

操作步骤见[组合指南](<../guide/composition.md>)、[首次计划](<../tutorial/first-plan.md>)；源码见 [presets](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/presets.py#L31>)。
