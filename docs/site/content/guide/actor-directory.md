# 让驱动复用独立主体目录

同一组参与者可能同时加入对话与社交机制，也可能在不同阶段选择不同驱动。先保存一份持久主体目录，再把驱动工厂绑定到目录中的标签。先了解[主体教程](../tutorial/actors.md)和[插件依赖](plugins.md)。

## 一、创建目录并绑定驱动

以下是可直接运行的完整装配检查，保存为 `directory_check.py`。它检查人口资料与驱动解析，无需模型服务。

```python title="directory_check.py"
import asyncio
import sys
from society0 import ActorRecord, DriverResult, compose
from society0.plugins import actor_data_plugin, actor_plugin, rule_driver_plugin


def decide(session):
    return DriverResult("completed", {"actor": session.actor.id})


async def main(path):
    plugins = (
        actor_data_plugin(name="population", records=(
            ActorRecord("alice", "rule", persona="核对资料来源。", roles=("reader",)),
            ActorRecord("bob", "rule", persona="关注资料中的时间。", roles=("reader",)),
        )),
        rule_driver_plugin(decide, name="reader_driver"),
        actor_plugin({"rule": ("reader_driver", "factory")},
                     data=("population", "directory")),
    )
    async with compose(path, plugins) as host:
        directory = host.service("population", "directory")
        actors = host.service("actors", "actors")
        assert actors.directory is directory
        page = directory.select(role="reader", limit=10)
        assert page.total == 2
        assert actors["alice"].id == "alice"
        print({"total": page.total, "driver_names": directory.driver_names()})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

```bash
uv run python directory_check.py runs/directory-check
```

应看到两位参与者和 `rule` 标签。实际激活放入 RunPlan 的阶段；取得目录资料本身不触发模型请求。

## 二、给机制使用同一目录

`actor_data_plugin` 拥有身份、persona、config、state 和选择索引；`actor_plugin` 发布运行映射并按取用的主体建立驱动。例子中的目录名是 `population`，所以接入轮次对话或社交配方时传 `actors="population"`。默认目录名为 `actors.data`。

下面是添加到上述插件序列的接线片段，另需同一主机中的 interaction：

```python
from society0.plugins import round_robin_plugin

conversation = round_robin_plugin(
    ("alice", "bob"), group_size=2, actors="population", name="conversation"
)
```

机制在初始化时要求成员已经存在，因此它声明对人口数据的 `schema_requires`；驱动可再依赖机制提供的认知服务。这样主体数据初始化与运行驱动接线各有明确顺序。

## 三、更新与恢复检查

使用 `directory.set_state("alice", "关注事项", "发布日期")` 更新一个主观状态，或用 `update` 集中替换有关字段。在变更后重新取得短视图。客观世界的交易、帖子和消息由各领域机制保存；persona 用于稳定背景，state 表达主体的主观状态。

恢复时传入相同数据声明和可解析的驱动映射。来源运行中的 `driver` 标签继续存在，新工厂必须覆盖它们；漏掉标签会在装配时报告 unresolved actor drivers。更改模型配置或研究条件时使用新运行目录，并把有效配置记录到 RunContract。

验证恢复后的人口总数、角色选择、persona/state 和驱动标签，再对同一主体做一次短激活。`select` 的结果带总数和续页游标，人口较多时读完全部页。外部目录协议及公开方法见[主体参考](../reference/actor.md)与 [ActorDirectory / ActorStore 源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/actors.py#L102-L320)。
