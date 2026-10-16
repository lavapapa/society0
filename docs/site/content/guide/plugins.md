# 创建一个机制插件

要给研究加一张自己的表和一个领域服务时，从这个计数器插件开始：它把计数写进共享存储，走完声明、初始化、取得服务和退出这几步。先完成[基础安装](../start/installation.md)。计数器不调用模型；要让主体通过行动改动它，接着看[行动教程](../tutorial/actions.md)。

## 写一个计数器插件并跑起来

把下面的完整程序保存为仓库根目录的 `counter_study.py`，每次执行挑一个新的输出路径。

```python title="counter_study.py"
import asyncio
import sys
from society0 import compose
from society0.kernel.plugins import Plugin


class Counter:
    def __init__(self, store):
        self.store = store

    def value(self):
        return self.store.read(
            lambda view: view.query("SELECT value FROM study_counter WHERE id=1")[0][0]
        )

    def add(self, amount):
        self.store.transaction(lambda writer: writer.execute(
            "UPDATE study_counter SET value=value+? WHERE id=1", (amount,)
        ))


def counter_plugin():
    def initialize(writer):
        writer.execute("INSERT INTO study_counter VALUES(1, 0)")

    def install(context):
        context.provide("counter", Counter(context.require("storage", "store")))

    return Plugin(
        "counter", requires=("storage",), install=install,
        schema=("CREATE TABLE study_counter(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)",),
        initialize=initialize,
    )


async def main(path):
    async with compose(path, (counter_plugin(),)) as host:
        counter = host.service("counter", "counter")
        assert counter.value() == 0
        counter.add(3)
        assert counter.value() == 3
        print({"value": counter.value()})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

```bash
uv run python counter_study.py runs/counter-check
```

预期打印 `{'value': 3}`。这次运行只验证服务装配和 SQL 写入本身；要让计数器进入可恢复的业务步骤，把插件放进[运行计划](../tutorial/first-plan.md)，由阶段调用它，再交给 Runtime 发布完整步骤。

## 数据先决与服务依赖各管什么

`requires` 决定安装函数能用 `context.require` 借用哪些服务，`schema_requires` 决定建表和写初始数据的先后。举例来说，新插件在初始化时要写一条引用主体的外键，就把主体数据插件声明为数据先决；安装阶段还要借它的目录，就同时声明服务依赖。

下面是已有插件声明的装配片段，`install`、`schema`、`initialize` 由该机制自己定义：

```python
plugin = Plugin(
    "membership", requires=("storage", "actors.data"),
    schema_requires=("actors.data",),
    install=install, schema=schema, initialize=initialize,
)
```

`compose` 先按数据图创建共享 schema，在初始事务里跑同步初始化，再按服务图安装资源。恢复时沿用来源那份完整步骤里的数据，跳过初始化。外部文件需要异步预读时，用 `prepare` 返回上下文管理器和同步初始化函数；准备资源在服务安装前关闭。完整用法见[数据集](datasets.md)。

## 创建资源时一并登记清理

资源一创建出来，就把清理登记到当前插件上下文。下面是安装函数里的装配片段，`provider` 与 `action` 由该机制创建：

```python
context.on_close(provider.close)
information = context.require("interaction", "information")
actions = context.require("interaction", "actions")
context.on_close(information.mount("/reports", provider))
context.on_close(actions.register(action, dependencies=("study_reports",)))
```

`mount` 和 `register` 会返回注销函数，用来撤销这次注册。默认挂载时 provider 仍归创建它的插件所有，关闭也由该插件负责；传 `owned=True` 则由 Information 托管到路由器关闭，两边不要重复安排所有者。`on_close` 按后登记先执行的次序清理，所以上例先撤回行动和路由，再关闭 provider。

服务在运行中持有任务时，先停任务，再释放它依赖的资源。`on_quiesce(..., stage="runtime")` 交给 Runtime 收束阶段执行，worker 阶段用于后台工作，资源关闭排在最后。研究插件按实际所有权登记清理，普通纯值服务不必补一个空函数。

## 换成自己的机制再检查装配

先用一小段规则运行检查初始事实、服务引用和一项领域操作，确认无误再加模型主体。运行清单里能看到插件名、直接依赖和展开关系；安装时报缺少依赖，就改实际消费者的声明。名字重复时给独立实例换成不同的名字，并同步改掉引用它的地方。

“先配对、后发送”这类业务顺序由 [Schedule](schedule.md) 安排。插件需要在步骤边界做事时，在安装期间登记 `context.on_step(before=..., after=...)`，正式 Runtime 会收集这些回调。完整参数与生命周期见[插件参考](../reference/plugin.md)和 [Plugin 与 PluginContext 源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/plugins.py#L16-L100)。
