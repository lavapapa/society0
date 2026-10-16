# Plugin 与资源主机

`from society0.kernel.plugins import Plugin, PluginContext, PluginHost` 用于声明机制、借用服务并管理一次运行的资源。数据库装配使用 [compose](<composition.md>)。

## 一、声明

插件的静态声明决定依赖检查与安装顺序。

<!-- api: society0.kernel.plugins.Plugin -->
<!-- api: society0.kernel.plugins.PluginHost -->
<!-- api: society0.kernel.plugins.PluginHost.describe_plugins -->

`includes` 递归展开到同一名称空间；每个实例名称必须唯一，共享子插件应声明一次。`requires` 是服务安装 DAG；`schema_requires` 是数据准备与初始化 DAG。父插件使用子服务仍须声明 `requires`，传递依赖也须显式列入。重复名称、缺依赖或任一图成环抛出 `ValueError`；compose 在创建运行目录前检查这些错误。

Host 串行安装服务，不执行 schema 或 initialize。数据入口由 compose 消费；需要异步取得外部数据时，`prepare()` 返回上下文管理器，其产物为同步初始化函数。业务因果顺序交给 Phase，不依赖无关联插件的安装次序。

`describe_plugins()` 返回不可变描述元组，按父项先于子项的声明展开顺序排列；构造后与关闭后均可读取。它描述静态关系，不包含服务对象。

## 二、服务

安装函数可同步或异步，服务按插件实例名分区。

<!-- api: society0.kernel.plugins.PluginContext.provide -->
<!-- api: society0.kernel.plugins.PluginContext.require -->
<!-- api: society0.kernel.plugins.PluginHost.service -->

```python
def install(ctx):
    store = ctx.require('storage', 'store')
    ctx.provide('ledger', Ledger(store))
```

此片段中的插件须声明 `requires=('storage',)`，`Ledger` 由领域实现提供。`provide` 交付原对象引用，同插件重名抛 `ValueError`。`require` 对未声明依赖抛 `ValueError`，服务缺失抛 `KeyError`。应用通过就绪 Host 的 `service` 取服务；启动前或关闭后调用抛 `RuntimeError`。服务对象的并发与读写语义由提供方定义，借用者须在主机生命周期内使用。

## 三、收束

资源拥有者取得资源后立即登记释放，借用者保留显式依赖。

<!-- api: society0.kernel.plugins.PluginContext.on_close -->
<!-- api: society0.kernel.plugins.PluginContext.enter_context -->
<!-- api: society0.kernel.plugins.PluginContext.on_step -->
<!-- api: society0.kernel.plugins.PluginContext.on_quiesce -->

`on_close` 接受同步或异步回调，按登记逆序执行；`enter_context` 接受两类上下文管理器。提供服务、登记关闭、登记步骤与停止回调均限安装期间。安装失败、取消及作用域异常会清理已登记资源；一个清理失败后继续清理其余资源，异常沿 Python 异常链传播。管理器的异常抑制返回值不会吞掉原业务失败。

`on_step` 的 before/after 是零参数回调，Runtime 每步固定整组回调，再执行 before、阶段、after 与完整发布。Host 退出不执行这些业务钩子。`on_quiesce` 先收束 runtime 阶段，再收束 worker 阶段，最后释放普通资源；两阶段内部均逆序执行，非法 stage 抛 `ValueError`。后台任务须在依赖关闭前结束。

Host 是一次性异步作用域。关闭不撤销已经发生的网络调用或外部事实；释放等待成本由已登记任务和回调决定。

继续阅读[插件指南](<../guide/plugins.md>)；源码入口为 [PluginContext](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/plugins.py#L43>)。
