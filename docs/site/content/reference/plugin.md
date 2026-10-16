# Plugin 与资源主机

`from society0.kernel.plugins import Plugin, PluginContext, PluginHost` 用来把一次运行的机制声明成插件，并在同一个异步作用域里安装服务、登记释放。谁取得资源，就在安装时登记由谁释放；只是借用别人服务的插件，把依赖写进声明即可。这些声明会在创建运行目录之前被检查一遍。建立和恢复共享数据库由 [compose](<composition.md>) 负责。

## 静态声明与依赖检查

插件的静态声明决定依赖怎么检查、服务按什么顺序安装。

<!-- api: society0.kernel.plugins.Plugin -->
<!-- api: society0.kernel.plugins.PluginHost -->
<!-- api: society0.kernel.plugins.PluginHost.describe_plugins -->

`includes` 会递归展开到同一名称空间，每个实例的名称必须唯一，共享的子插件只声明一次。`requires` 决定服务按什么顺序安装，`schema_requires` 决定数据准备和初始化的顺序，两者各自排成一张依赖图。父插件要用子插件的服务，同样得在 `requires` 里写明；隔着几层的传递依赖也要显式列出。名称重复、依赖缺失或任一张图出现环时抛 `ValueError`，compose 会在创建运行目录之前把这些错误检查出来。

Host 按顺序安装服务，建表和初始化都不归它做。数据入口留给 compose 使用；建数据之前要先异步取外部资料时，让 `prepare()` 返回上下文管理器，它产出的函数是同步的初始化函数。业务上的先后顺序交给 Phase 表达，不要依赖互不相关的插件谁先安装。

`describe_plugins()` 返回一个不可变的描述元组，顺序是声明展开后的父项在前、子项在后；刚构造好和关闭之后都能读。里面只有静态关系，没有服务对象。

## 服务的提供与取得

安装函数可以同步也可以异步，服务按插件实例名分区。

<!-- api: society0.kernel.plugins.PluginContext.provide -->
<!-- api: society0.kernel.plugins.PluginContext.require -->
<!-- api: society0.kernel.plugins.PluginHost.service -->

```python
def install(ctx):
    store = ctx.require('storage', 'store')
    ctx.provide('ledger', Ledger(store))
```

这段安装函数所在的插件必须声明 `requires=('storage',)`，`Ledger` 由你的领域实现提供。`provide` 交出去的是原对象引用，同一个插件里重名会抛 `ValueError`。`require` 遇到没有声明过的依赖抛 `ValueError`，声明了但服务不存在抛 `KeyError`。运行中的应用通过就绪 Host 的 `service` 取服务，Host 启动前或关闭后调用会抛 `RuntimeError`。服务对象能不能并发使用、读写语义如何，由提供方决定；借用者只能在主机存活期间使用它。

## 资源释放与退出顺序

取得资源的人要在安装阶段就登记好怎么释放，借用的人保留显式依赖。

<!-- api: society0.kernel.plugins.PluginContext.on_close -->
<!-- api: society0.kernel.plugins.PluginContext.enter_context -->
<!-- api: society0.kernel.plugins.PluginContext.on_step -->
<!-- api: society0.kernel.plugins.PluginContext.on_quiesce -->

`on_close` 接受同步或异步回调，按登记的逆序执行；`enter_context` 同步和异步的上下文管理器都能接。提供服务、登记关闭、登记步骤回调和停止回调，都只能在安装期间做。安装失败、任务取消或作用域内出现异常时，已经登记的资源都会被清理；某一个清理失败后，其余的继续清理，异常按 Python 的异常链往上抛。上下文管理器即使返回了抑制异常的值，也不会把原来的业务失败吞掉。

`on_step` 的 before/after 是不带参数的回调。Runtime 在每一步开始前取定整组回调，再依次执行 before、各个阶段、after 和完整发布。Host 退出时不会执行这些业务钩子。`on_quiesce` 分两步停止：先停 runtime 阶段登记的回调，再停 worker 阶段的回调，最后释放普通资源；两个阶段内部都按逆序执行，stage 传入其他值会抛 `ValueError`。后台任务必须在依赖关闭之前结束。

Host 是一次性的异步作用域。关闭它不会撤销已经发出的网络调用或已经发生的外部事实；退出要等多久，取决于登记了哪些任务和回调。

继续阅读[插件指南](<../guide/plugins.md>)；源码入口为 [PluginContext](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/plugins.py#L43>)。
