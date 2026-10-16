# 创建一个机制插件

本页将一个实验计数器接入共享存储，验证声明、初始化、服务取得和退出流程。先完成[基础安装](../start/installation.md)。该机制不调用模型；要让主体通过行动修改它，可继续阅读[行动教程](../tutorial/actions.md)。

## 一、定义并检查服务

将下面完整程序保存为仓库根目录的 `counter_study.py`。每次执行选择新的输出路径。

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

预期打印 `{'value': 3}`。这是服务装配与 SQL 写入检查；要发布可恢复的业务步骤，将插件放入[运行计划](../tutorial/first-plan.md)，由阶段调用它，交给 Runtime 收束并完成步骤。

## 二、分清两类依赖

`requires` 允许安装函数通过 `context.require` 借用直接依赖的服务。`schema_requires` 控制表和初始数据的先决顺序。例如新插件初始化时写入引用主体的外键，应声明主体数据插件为数据先决；安装时借用其目录，则同时声明服务依赖。

以下为已有插件声明的接线片段，`install`、`schema`、`initialize` 由该机制定义：

```python
plugin = Plugin(
    "membership", requires=("storage", "actors.data"),
    schema_requires=("actors.data",),
    install=install, schema=schema, initialize=initialize,
)
```

`compose` 先按数据图创建共享 schema、在初始事务中执行同步初始化，再按服务图安装资源。恢复时使用来源完整点的数据，跳过初始化。外部文件需要异步预读时，可用 `prepare` 返回上下文管理器及同步初始化函数；准备资源在服务安装前关闭。完整用法见[数据集](datasets.md)。

## 三、登记并释放资源

创建资源后立即把清理交给当前插件上下文。下面是安装函数中的接线片段，`provider` 与 `action` 由机制创建：

```python
context.on_close(provider.close)
information = context.require("interaction", "information")
actions = context.require("interaction", "actions")
context.on_close(information.mount("/reports", provider))
context.on_close(actions.register(action, dependencies=("study_reports",)))
```

`mount` 和 `register` 返回本次贡献的注销函数。默认挂载借用 provider，关闭由创建它的插件负责；选择 `owned=True` 时由 Information 托管至路由器关闭，避免重复安排所有者。`on_close` 按后登记先执行的次序清理，上例先撤回行动与路由，再关闭 provider。

运行中持有任务的服务需要先停止任务，再释放其依赖。`on_quiesce(..., stage="runtime")` 用于 Runtime 收束，worker 阶段用于后台工作，最后执行资源关闭。研究插件按实际所有权登记，普通纯值服务无须添加空清理函数。

## 四、接入研究计划

先用短规则运行检查初始事实、服务引用及一项领域操作，再增加模型主体。检查运行清单中的插件名、直接依赖与展开关系；若安装报缺少依赖，修改实际消费者的声明。名字重复时，给独立实例分配不同名字，并更新对应服务引用。

业务上的“先配对、后发送”由 [Schedule](schedule.md) 明确安排。插件需要步骤收尾时，在安装期间登记 `context.on_step(before=..., after=...)`，正式 Runtime 会收集这些回调。参阅[插件参考](../reference/plugin.md)及 [Plugin 与 PluginContext 源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/plugins.py#L16-L100)查阅完整参数与生命周期。
