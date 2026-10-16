# 主体与驱动

上一课的 Alice 和 Bob 只有名字和一个驱动标签，规则返回的也仅有主体名与时间。这一课给两位主体补上背景、主观状态和角色，写进持久主体目录，再让规则把这些资料读出来。跑完之后，结果表里会分别出现 Alice 的“活动时间”和 Bob 的“活动内容”。改的仍是[自己的运行计划](first-plan.md)那份 `my_simulation.py`。

## 替换片段：给两位主体补上资料

打开上一课的 `my_simulation.py`。下面这段要加在 `build` 里面、`actors` 定义之后：

```python title="加入 build 的片段"
records = [
    ActorRecord(
        "alice", "rule",
        persona="社区图书馆志愿者，关注活动是否便于参加。",
        state={"关注事项": "活动时间"},
        roles=("participant", "organizer"),
    ),
    ActorRecord(
        "bob", "rule",
        persona="附近居民，想了解图书馆的新活动。",
        state={"关注事项": "活动内容"},
        roles=("participant",),
    ),
]
```

把 `actor_plugin` 原来的 `records=[ActorRecord(...)]` 参数改成 `records=records`。本课只用两位主体；上一课若加了 Carol，把 `actors` 改回 `("alice", "bob")`。

`id` 把主体和之后的行动、Thread、结果连起来。`driver="rule"` 是存在目录里的标签，装配中的 `{"rule": ("rule_driver", "factory")}` 负责把它解析成具体驱动。`persona` 写背景，`state` 存会变化的主观状态，`roles` 用来挑选参与者。

## 替换片段：让规则读出背景和关注点

用下面这段替换原来的 `rule` 函数。主体由持久 ActorStore 创建，它放在 `config` 里的是本次使用的记录视图，因此规则可以读到 persona 和 state。

```python title="替换 rule"
def rule(session):
    record = session.actor.config
    return DriverResult("completed", {
        "actor": record.id,
        "time": session.moment.time,
        "background": record.persona,
        "concern": record.state_values(("关注事项",))["关注事项"],
    }, "rule")
```

配置仍用 `runs/hello/config.json`，输出换到新目录：

```bash
uv run python -m society0.kernel.runner \
  --factory my_simulation:build \
  --config runs/hello/config.json \
  --output runs/hello/personas
```

把首次运行的读取脚本指向 `runs/hello/personas`。两条结果里应分别出现“活动时间”和“活动内容”，各自的完整背景也一起保留。资料只有在进入驱动的实际输入之后才影响决定，这段规则把读取的位置明写了出来。

## 完整程序：不启动驱动，只读目录

下面这份完整程序保存为 `inspect_actors.py` 后执行。它通过只读连接打开已经保存的主体，全程不启动驱动。

```python title="inspect_actors.py"
from society0.kernel.actors import ActorDirectory
from society0.kernel.storage import StageReader

with StageReader("runs/hello/personas") as reader:
    directory = ActorDirectory(reader)
    page = directory.select(role="participant", limit=1)
    print("参与者总数：", page.total)
    while True:
        print(page.items)
        if page.next_cursor is None:
            break
        page = directory.select(
            role="participant", limit=1, cursor=page.next_cursor,
        )
```

```bash
uv run python inspect_actors.py
```

预期总数是 2，随后两页各显示一个主体 ID。选择结果给调度提供候选 ID，真正激活谁仍由阶段函数安排；某个主体能不能做某个行动，由机制在执行时判断。

## 替换片段：把同一份目录交给更多机制

当前的 `actor_plugin` 自带一个数据目录，对话、社交等机制都可以依赖它。要把目录单独拿出来共用，就在 imports 里加入 `actor_data_plugin`，再用下面的**替换片段**替掉原来的 `actor_plugin` 项：

```python title="替换原 actor_plugin 项为两项"
actor_data_plugin(records=records, name="people"),
actor_plugin(
    {"rule": ("rule_driver", "factory")},
    data=("people", "directory"),
),
```

这两项仍放在原来的 `plugins` 元组里。别的机制把 `actors` 写成 `"people"`，就会读到同一份资料。从外部文件加载目录的完整要求见[主体目录指南](../guide/actor-directory.md)。

运行期间改一个主观字段用 `set_state`，整块替换状态用 `update(state=...)`。改完要重新取一次视图，旧视图会因为版本变化而失效。恢复时存储里的主体和驱动标签都保留，新运行必须有一份能解析这些标签的工厂。

接下来进入[轮次对话](conversation.md)，让同一组主体在两个交流机制中留下真实消息。
