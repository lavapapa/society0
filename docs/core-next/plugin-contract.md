# 插件主机合同

插件主机管理一组显式依赖及其资源生命周期。它是新内核的独立基础，尚未连接 World、Agent、信息视图或仿真调度。所有服务保存在当前主机实例内；多次运行分别建立主机。

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

`Plugin(name, requires=(), install=...)` 的 `requires` 是插件名称元组。安装前一次性检查重复插件、缺失依赖和依赖环；其中任一错误都在首个安装函数运行前抛出 `ValueError`。依赖排序使用标准库 `TopologicalSorter`，安装过程串行进行。互相独立插件的业务行为应独立于彼此安装次序。

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
