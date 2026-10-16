# 发现与执行行动

这一课改写对话主体的规则：先发现自己能做什么，再读取参数描述，最后发送消息并检查结果。继续使用[轮次对话](conversation.md)的完整工厂，数据、配对和阶段安排保持原样。

## 一、建立自己的对话程序

在仓库根目录复制完整示例，之后只编辑副本：

```bash
cp examples/core_next/conversation_pilot.py conversation_study.py
```

将副本中的 `async def talk(session)` 整段替换为下面代码。所需的 `Ref`、`DriverResult` 已在副本 imports 中。

```python title="替换 talk 函数"
async def talk(session):
    sent = []
    for name in ("work", "commons"):
        target = Ref(name, "participants", session.actor.id)
        action_name = name + ".send_message_to_partner"
        page = await session.actions.find(target, query="发送", limit=1)
        names = [item.name for item in page.items]
        while page.next_cursor is not None:
            page = await session.actions.find(
                target, query="发送", limit=1, cursor=page.next_cursor,
            )
            names.extend(item.name for item in page.items)
        if action_name not in names:
            return DriverResult("incomplete", {"available": names}, "no_send_action")

        detail = await session.actions.describe(action_name, target)
        assert "content" in detail.parameters["properties"]
        result = await session.actions.invoke(action_name, target, {
            "content": f"{session.actor.id} 想讨论图书馆活动的时间。",
        })
        if result.status != "completed":
            return DriverResult("incomplete", result.value, "message_not_completed")
        sent.append({
            "mechanism": name, "actor": session.actor.id,
            "action": detail.name, "status": result.status, **result.value,
        })
    return DriverResult("completed", sent, "rule")
```

目标 `Ref` 来自对话机制的公开 `participants` 资源，key 是自己的主体 ID。发现返回轻量摘要；描述返回参数 schema；调用沿用原来的 target，并把正文放进 `content`。

## 二、运行并核对

```bash
uv run python -m society0.kernel.runner \
  --factory conversation_study:build \
  --config runs/conversation/config.json \
  --output runs/conversation/discovery
```

把上一课的 `inspect_conversation.py` 路径改成 `runs/conversation/discovery`，再次执行。第 1 步仍有 8 条回执，每条额外包含实际行动名称与 `status="completed"`。把[信息读取脚本](files.md)也指向新目录，可以读到图书馆活动的新正文。

发现与执行之间，世界可能变化。即使某行动刚出现在页面中，执行入口仍会重验权限、参数与当前资格；本例在已开始的当前轮发送，消息由机制事务写入。

## 三、观察一次正常拒绝

在自己的副本中，将 `content` 暂时改为只有空格的字符串，使用新的输出目录运行。对话机制返回 `rejected`，理由为 `empty_content`；本课规则将它解释为任务未完成，于是返回 `DriverResult("incomplete", ...)`。

默认阶段会因此失败，运行器非零退出，当前步没有发布完整身份。这个练习说明两个判断需要分别设计：机制说明动作是否完成，Driver 说明主体此次任务是否完成。其他研究也可以在收到拒绝后改选行动或等待后续信号。

| 动作状态 | 主体应如何理解 |
| --- | --- |
| `completed` | 操作已完成，可以消费返回值 |
| `accepted` | 请求已受理，可通过过程引用继续追踪 |
| `rejected` | 本次没有完成操作，结合原因重新决定 |

领域处理器抛出的异常会使整步失败，留给恢复流程处理。正常业务拒绝由 `ActionResult` 表达。注册为 terminal 的动作还要求成功完成才具有终止效果；LLM 的完成名称、标签策略见[模型参考](../reference/llm.md)。

## 四、从使用到扩展

本课复用已有模板，避免在主体规则里直接改消息表。新增业务行动时，机制插件负责 schema、资格和规范写入器，并托管注册返回的注销函数，步骤见[插件指南](../guide/plugins.md)。

至此规则主体已经能读取完整资料并调用真实操作。接下来可以进入[模型驱动](llm.md)观察同样入口如何成为工具循环，或直接学习[记录与读取结果](results.md)。
