# 主体与驱动

Alice 和 Bob 可以参与同一场讨论，同时保留各自的背景与关注点。这一课修改[自己的运行计划](first-plan.md)，把这些差异写入持久主体目录，再让规则实际读取它们。

## 一、给主体加入资料

打开上一课的 `my_simulation.py`。在 `build` 内、`actors` 定义后加入以下片段：

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

把原 `actor_plugin` 的 `records=[ActorRecord(...)]` 参数改成 `records=records`。本课继续使用两位主体；若上一课加了 Carol，请把 `actors` 恢复为 `("alice", "bob")`。

`id` 关联主体之后的行动、Thread 和结果。`driver="rule"` 是持久标签，装配中的 `{"rule": ("rule_driver", "factory")}` 负责解析它。`persona` 描述背景，`state` 保存会变化的主观状态，`roles` 用于选择参与者。

## 二、让规则使用资料

用下面片段替换原来的 `rule` 函数。持久 ActorStore 创建的 Actor，其 `config` 保存本次使用的记录视图，因此可以读取 persona 与 state。

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

沿用配置，在新目录执行：

```bash
uv run python -m society0.kernel.runner \
  --factory my_simulation:build \
  --config runs/hello/config.json \
  --output runs/hello/personas
```

把首次运行的读取脚本指向 `runs/hello/personas`。两条结果应分别包含“活动时间”和“活动内容”，并保留各自完整背景。资料只有进入驱动的实际输入后才影响决定；这段规则明确展示了读取位置。

## 三、查看持久目录

保存并执行下面完整脚本。它通过只读连接读取已保存的主体，没有启动驱动。

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

预期总数为 2，随后两页各显示一个主体 ID。选择页给调度提供候选 ID；实际激活仍由阶段调用安排。具体行动资格由机制在执行时判断。

## 四、把目录交给多个机制

当前 `actor_plugin` 自动包含一个数据目录。对话、社交等机制都可以依赖这份目录；需要显式组织装配时，采用以下**替换片段**，在 imports 加入 `actor_data_plugin`：

```python title="替换原 actor_plugin 项为两项"
actor_data_plugin(records=records, name="people"),
actor_plugin(
    {"rule": ("rule_driver", "factory")},
    data=("people", "directory"),
),
```

这两项仍放在原 `plugins` 元组中。给其他机制指定 `actors="people"`，它们就借用相同资料。完整外部目录要求见[主体目录指南](../guide/actor-directory.md)。

运行期间用 `set_state` 修改一个主观字段，用 `update(state=...)` 替换整个状态；修改后重新取得视图，原视图会因版本改变而失效。恢复会保留存储里的主体与驱动标签，新运行必须提供能够解析这些标签的工厂。

接下来进入[轮次对话](conversation.md)，让同一组主体在两个交流机制中留下真实消息。
