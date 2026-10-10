# 插件主机合同

插件主机管理一组显式依赖及其资源生命周期。组合入口通过它安装存储、主体、信息和调度服务；主机本身负责依赖与资源生命周期。所有服务保存在当前主机实例内；多次运行分别建立主机。

## 一、使用

插件声明自己的名称、直接依赖的插件名称和安装函数。安装函数接受上下文，可以同步执行，也可以返回可等待对象。主机统一通过异步上下文管理器启动与关闭。

```python
import asyncio
from society0.kernel import Plugin, PluginHost


def install_data(ctx):
    connection = open("example.txt", "a", encoding="utf-8")
    ctx.on_close(connection.close)
    ctx.provide("writer", connection)


async def install_report(ctx):
    writer = ctx.require("data", "writer")
    ctx.provide("write", writer.write)
    ctx.on_close(writer.flush)


async def main():
    plugins = [
        Plugin("report", requires=("data",), install=install_report),
        Plugin("data", install=install_data),
    ]
    async with PluginHost(plugins) as host:
        host.service("report", "write")("example\n")


asyncio.run(main())
```

该例创建本地 `example.txt`。主机按依赖先安装 data，再安装 report；退出时先刷新，再关闭文件。实际插件可以直接注册自己已有的服务对象，主机不复制服务或 World。

## 二、依赖

`Plugin` 的 `requires` 是服务依赖插件名称元组。`includes` 接受静态子插件集合，主机递归展开到同一个实例空间；包含关系负责装配，父插件借用子服务仍须明确声明 `requires`。同类插件多实例使用工厂参数生成显式名称与表前缀。共享子插件在配方中声明一次，其他插件通过具名依赖借用它。

`schema_requires` 声明数据准备与初始化的先决插件，独立于服务图。安装前检查展开后的重复插件、缺失依赖和两个图的环；其中任一错误都在首个安装函数运行前抛出 `ValueError`，`compose` 在创建运行目录前执行同样检查。依赖排序使用标准库 `TopologicalSorter`，安装过程串行进行。互相独立插件的业务行为应独立于彼此安装次序。省略 `install` 的插件可以声明纯数据；初始化与恢复见 [组合合同](composition-contract.md)。

`ctx.provide(service, value)` 将服务放入本插件名称空间。同插件重复提供同名服务会抛出 `ValueError`；不同插件可以提供同一个局部服务名。`ctx.require(plugin, service)` 限于当前插件显式声明的直接依赖，未声明依赖抛出 `ValueError`，未提供的服务抛出 `KeyError`。传递依赖需要明确加入 `requires` 后再借用，避免隐藏生命周期关系。

`host.service(plugin, service)` 供应用在主机就绪期间取得服务。启动前或关闭后访问会抛出 `RuntimeError`。服务对象本身的类型、调用并发和返回值由提供方定义；Python 对象引用一旦交付，持有者须遵守资源生命周期。

## 三、关闭

`ctx.on_close(callback, *args, **kwargs)` 注册同步或异步清理函数；返回可等待对象时主机会等待完成。`await ctx.enter_context(manager)` 接受同步或异步上下文管理器。资源取得成功后应立即登记清理，管理器进入阶段失败前自行取得的资源由其实现负责清理。

登记使用标准库 `AsyncExitStack`，严格按登记逆序关闭。依赖提供方先安装，因此消费者先关闭；消费者清理函数仍可调用 `ctx.require`。所有清理执行后清空服务登记，保留下来的上下文借用将失效。注册服务或追加清理仅在该插件的安装函数执行期间有效。

安装失败、安装中取消、主机作用域内异常或取消，都会进入清理；某个清理函数抛出异常后，其余已登记清理仍会执行。每个管理器收到作用域原始异常参数；主机忽略其异常抑制返回值，安装或业务异常继续向调用方传播。各管理器通过独立退出回调注册，其他资源的清理失败不会传入当前管理器供其抑制。清理自身失败时按标准 Python 异常链传播。清理函数需自行保证可终止，调用方反复取消清理任务可能中断异步资源释放。

主机为一次性作用域，退出后重新运行须创建新实例。同一实例的生命周期由一个任务拥有；它不提供并发启动或外部并发关闭合同。清理是释放连接、文件和后台任务等资源；已发送请求、已写入外部系统的数据及仿真历史，遵循各插件自己的业务提交合同。

## 四、验收

作者测试位于 `tests/primary/test_kernel_plugins.py`。首轮失败记录 `research/core-next/plugin-host-red.txt` 保留模块尚未实现时的导入失败，随后实现对应合同并保留 `plugin-host-green.txt`。测试覆盖依赖预检、服务冲突、依赖可见性、两个主机隔离、同步和异步管理器、部分安装失败、安装与作用域取消、清理异常及异常吞掉风险。独立审查另用 `test_kernel_plugins_review.py` 验证生命周期边界。

这组验收证明插件主机的局部合同；完整 Core 的状态、调度、模型调用与持久化需由后续消费者接入并分别验证。

## 完整步骤与资源收束

机制在安装时调用 `context.on_step(before=..., after=...)` 声明零参数同步或异步步骤回调。回调按依赖安装顺序登记；`context.step_hooks()` 在 Host 就绪后返回 before、after 两个元组，每项为 `(plugin_name, callback)`。Runtime 在实际运行步骤时取得完整集合，因此 Runtime 先于其他机制安装也能看到后续登记。安装结束后声明冻结，Host 退出不执行领域步骤回调。

需要先停止运行任务的服务使用 `context.on_quiesce(callback)`。Host 先按逆序收束这些任务，再按原资源栈逆序关闭机制、共享模型和存储。某个收束回调失败仍会继续其余收束与资源释放，异常向外传播。步骤回调与关闭回调具有不同用途：前者参与完整步骤，后者释放本次运行拥有的资源。

## 外部初始化

`compose` 汇集插件的静态 `schema`，并在根事务内按 `schema_requires` 的数据先决顺序调用 `initialize(writer)`。普通初始化保持同步。需要异步下载或外部资源的插件可以提供 `prepare()`，返回同步或异步上下文管理器，产出一个同步初始化函数。准备阶段先取得数据，根事务统一写入，随后关闭准备资源，再安装运行服务。

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def prepare():
    async with open_source() as source:
        rows = await source.load()
        def initialize(writer):
            for row in rows:
                writer.execute("INSERT INTO facts VALUES(?,?)", row)
        yield initialize

plugin = Plugin("facts", schema=SCHEMA, prepare=prepare, install=install)
```

同一插件同时提供两个入口时，先执行普通 `initialize`，再执行准备所得函数。准备函数按数据先决顺序进入，其资源按逆序释放。后续准备失败、取消或根写入失败都会退出已取得的准备资源；根创建成功后写者立即交给外层资源栈，因此准备资源关闭时抛错也会关闭写者。此时已经发布的根仍是有效初始点，调用方收到清理异常。

准备所得闭包在进入正式运行前释放，外部数据无需随整个运行驻留。恢复依据完整状态重建服务，跳过 `prepare` 与 `initialize`。`examples/core_next/graph_environment.py` 展示异步读取外部图、将节点与边保存到共享 SQL，再用 NetworkX 和标准库数值数组生成派生视图；恢复时原始外部文件可以已经移除。派生视图的全图算法成本由所请求节点与边的规模决定。

图与数值派生的读取应按实际调度共享。例如阶段使用 `Phase('analysis', run, prepare=lambda context: graph.projection())`，各主体从 `session.prepared` 读取同一个 `(graph, values)`。该调用显式物化全部节点、边和数值数组，适合需要全图算法的阶段；它的成本由图规模决定，应由阶段准备承担。该共享引用是准备时点的派生结果，业务若要求看见随后写入，应按阶段模型重新准备相应视图。

## 事实与投影

`examples/core_next/typed_records.py` 给出一个可组合的实际记录机制。公开 `records.append` 行动追加事实，唯一 `(kind, key)` 拒绝重复事实，登记序号保持插入顺序；`records.project` 更新当前投影。两种行动在执行时重验主体归属。机制没有修改既有事实的公开行动，原生 SQL writer 则是受信的插件开发接口，承担该插件的领域规则。

示例的键域明确为整数和字符串，以类型列加文本键保存，整数 1 与字符串 "1" 不碰撞。每条值使用标准 JSON 编码，保留大整数、浮点值、列表顺序及完整文本；读取按原登记顺序还原。当前投影和不可变事实正文分表，小投影更新不会让 SQLite Session 捕获相邻冷正文。复合领域事件可以在同一个 `store.transaction(writer)` 中调用 `append_to` 和 `project_to`；回调失败时原生事务撤销全部写入，回调外的 writer 借用失效。

该例为显式 SQL 插件合同，未提供任意旧 Python dict 别名映射。被修改的单条 JSON 值仍按该值大小编码，巨型结构应由其领域 schema 拆分。`test_kernel_record_plugin.py` 验证类型与值精度、插入顺序、重复事实、无权主体、部分写入回滚、恢复，以及冷事实正文不进入小投影更新的捕获量。
