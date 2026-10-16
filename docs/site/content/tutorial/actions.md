# 发现与执行行动

上一课只是把资料读出来。这一课换掉对话主体的规则，让它在运行时先发现自己能做什么，再读参数描述，最后发送消息并检查结果。跑完之后，第 1 步的 8 条回执里会多出实际用到的行动名称和状态。工厂仍用[轮次对话](conversation.md)那一份，数据、配对和阶段安排都不动，副本存为 `conversation_study.py`。

## 复制工厂，换掉 talk 函数

在仓库根目录复制完整示例，之后只编辑副本：

```bash
cp examples/core_next/conversation_pilot.py conversation_study.py
```

把副本里的 `async def talk(session)` 整段换成下面的**替换片段**。用到的 `Ref`、`DriverResult` 副本已经 import 过。

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

目标的 `Ref` 指向对话机制公开的 `participants` 资源，key 是自己的主体 ID。发现只返回轻量摘要，描述返回参数 schema，调用时沿用同一个 target，正文放进 `content`。

## 用新工厂再跑一遍

```bash
uv run python -m society0.kernel.runner \
  --factory conversation_study:build \
  --config runs/conversation/config.json \
  --output runs/conversation/discovery
```

把上一课 `inspect_conversation.py` 里的路径改成 `runs/conversation/discovery` 再跑一次。第 1 步仍然是 8 条回执，每条多出实际用到的行动名称和 `status="completed"`。再把[信息读取脚本](files.md)指向新目录，就能读到关于图书馆活动的新正文。

从发现到执行之间，环境可能已经变了。某个行动刚刚出现在页面里，执行入口也会重新检查权限、参数和当前资格。这一例在已经开始的当前轮里发送，消息由机制在事务中写入。

## 空内容会被拒绝，看状态怎么传下去

在副本里把 `content` 暂时改成一段只有空格的字符串，换一个新输出目录运行。对话机制返回 `rejected`，理由是 `empty_content`；本课的规则把它当成任务没完成，于是返回 `DriverResult("incomplete", ...)`。

默认阶段遇到这个结果会失败，运行器以非零状态退出，当前这一步没有发布完整身份。这个练习说明两个判断要分开设计：机制管动作本身有没有完成，Driver 管主体这次任务有没有完成。换一个研究，也可以在收到拒绝之后改选别的行动，或者等后续信号。

| 动作状态 | 主体应如何理解 |
| --- | --- |
| `completed` | 操作已完成，可以消费返回值 |
| `accepted` | 请求已受理，可通过过程引用继续追踪 |
| `rejected` | 本次没有完成操作，结合原因重新决定 |

领域处理器抛出的异常会让整步失败，交给恢复流程处理；正常的业务拒绝用 `ActionResult` 表达。注册成 terminal 的动作，还要成功完成才算终止；LLM 的完成名称和标签策略见[模型参考](../reference/llm.md)。

## 自己加一个业务行动

这一课复用现成的模板，规则里没有直接去改消息表。自己加业务行动时，机制插件负责 schema、资格和规范写入，并保管注册时返回的注销函数，具体步骤见[插件指南](../guide/plugins.md)。

到这里，规则主体已经能读完整份资料并调用真实操作。接下来可以进入[模型驱动](llm.md)，看同一套入口怎么变成工具循环，也可以直接学[记录与读取结果](results.md)。
