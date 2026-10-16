# 让驱动复用独立主体目录

同一位参与者要同时出现在对话和社交机制里，或者不同阶段要换不同驱动时，把身份和主观资料存成一份持久目录，再让驱动按目录里的标签建立实例。先了解[主体教程](../tutorial/actors.md)和[插件依赖](plugins.md)。

## 建目录、绑驱动，跑一次装配检查

下面这段完整程序可以直接运行，保存为 `directory_check.py`。它检查人口资料和驱动解析，不需要模型服务。

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

应该看到两位参与者和 `rule` 标签。真正的激活放进 RunPlan 的阶段；单单取出目录资料不会触发模型请求。

## 让机制共用这份目录

`actor_data_plugin` 保存身份、persona、config、state 和选择索引；`actor_plugin` 发布运行映射，并在主体被取用时建立对应驱动。例子里的目录名是 `population`，所以接轮次对话或社交配方时要传 `actors="population"`。目录名不指定时默认为 `actors.data`。

下面这段装配片段加到上面的插件序列里，另外需要同一主机中的 interaction：

```python
from society0.plugins import round_robin_plugin

conversation = round_robin_plugin(
    ("alice", "bob"), group_size=2, actors="population", name="conversation"
)
```

机制在初始化时要求成员已经存在，所以它声明了对人口数据的 `schema_requires`；驱动还可以依赖机制提供的认知服务。主体数据初始化和运行驱动接线因此各有一条明确的先后。

## 改状态、做恢复检查

要改某位主体的主观状态，用 `directory.set_state("alice", "关注事项", "发布日期")`；要一次替换多个字段，用 `update`。改完再取一次短视图确认。客观世界里的交易、帖子和消息由各领域机制保存，persona 是稳定背景，state 记录主体的主观状态。

恢复时传入同样的数据声明和一份能解析的驱动映射。来源运行里的 `driver` 标签还在，新工厂必须把它们全部覆盖；漏掉某个标签，装配时会报 unresolved actor drivers。改了模型配置或研究条件就换新运行目录，并把实际配置记进 RunContract。

恢复后先核对人口总数、按角色的选择结果、persona/state 和驱动标签，再对同一位主体做一次短激活。`select` 的结果带总数和续页游标，人多的时候要把所有页读完。外部目录协议与公开方法见[主体参考](../reference/actor.md)和 [ActorDirectory / ActorStore 源码](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/actors.py#L102-L320)。
