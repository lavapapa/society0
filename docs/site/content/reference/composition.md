# compose 与配方

`from society0.kernel.composition import compose` 创建或恢复共享存储并安装机制；`from society0.presets import rule_preset, llm_preset` 提供常用单驱动组合。

## 一、装配

compose 返回异步上下文管理器，进入后得到就绪的 PluginHost。

<!-- api: society0.kernel.composition.compose -->

```python
async with compose(new_run, plugins, source=old_run, step=10) as host:
    runtime = host.service('runtime', 'runtime')
```

`new_run` 必须是新目标目录；省略 source 创建新根，提供 source 从完整点恢复，省略 step 选择源最新完整点。无 source 却提供 step 抛 `ValueError`。`storage` 是 compose 注入的保留实例名，提供同一个 StageStore。

装配先展开 includes、检查服务与数据两类 DAG，再按数据图汇集 DDL、进入 prepare 资源、在同一根事务内运行初始化。单插件同时提供 initialize 与 prepare 时，先运行普通初始化，再运行 prepare 产出的同步函数。返回 awaitable 的初始化抛 `TypeError`。准备资源在安装服务前逆序释放；随后按服务图安装服务。

恢复先将声明 DDL 编译并与源规范 schema 比较，不匹配时尚未复制源业务数据。恢复跳过 initialize 与 prepare，安装函数据恢复状态重建连接、索引或派生缓存。初始化失败不发布目标；根已成功发布后发生准备资源关闭或服务安装失败，则保留该有效根并释放已拥有资源。

退出依次收束任务、关闭主机服务和存储，不自动完成正在执行的步骤。根创建与恢复包含数据库复制；日常完整发布由 Runtime 负责。

## 二、配方

配方返回普通 Plugin 元组，可以直接交给 RunPlan；它们不启动运行。

<!-- api: society0.presets.rule_preset -->
<!-- api: society0.presets.llm_preset -->

`rule_preset` 组合交互、结果、规则驱动、主体映射、Runtime 和调用者提供的 schedule。`run` 回调应返回 DriverResult；`allows` 明确决定资源权限。`llm_preset` 另外加入 Thread，并要求显式 provider 与 input_builder 服务引用；模型与认知来源通过 plugins 加入。

两种配方均在 `records` 与外部 `data=(插件, 服务)` 中选择一种；同时提供抛 `ValueError`。规则主体的持久驱动标签使用 `rule`，模型主体使用 `llm`。外部目录负责初始记录。extensions 传给驱动扩展；昂贵共享资源交由对应插件拥有。

默认配方已包含具名基础服务；加入 plugins 时避免重复声明这些实例。`runtime=` 是完整插件替换入口，用于明确设置容量、预算或其他服务引用。配方没有隐式生成领域权限、时间线、模型端点或记忆策略，多驱动或多实例组合可直接声明 Plugin 元组。

操作步骤见[组合指南](<../guide/composition.md>)、[首次计划](<../tutorial/first-plan.md>)；源码见 [presets](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/presets.py#L31>)。
