# 从目录读到原文

上一课的运行里已经存下真实对话消息。这一课换成主体 `a` 的视角：先发现有哪些资料可以读，再查询消息目录，最后分段读完一条中文正文，打印出来的就是消息原文。动手前先完成[轮次对话](conversation.md)，保留 `runs/conversation/run`。

## 完整程序：以主体 a 的身份读取

把下面这份完整程序保存为 `read_messages.py`。它在已经停止的示例运行上搭起一条只读信息路由，用来演示身份绑定和资料访问；运行中的 Driver 不用这样搭，直接取 `session.information` 就行。

```python title="read_messages.py"
import asyncio

from society0 import Query
from society0.kernel.interaction import Information, InteractionScope, Moment
from society0.kernel.storage import StageReader
from society0.plugins.round_robin import round_robin_information


async def main():
    with StageReader("runs/conversation/run") as reader:
        information = Information(lambda *args: True)
        information.mount(
            "/work", round_robin_information(reader, "work"), owned=True,
        )
        try:
            with InteractionScope("a", Moment(2, "read")) as scope:
                tools = information.bound(scope)
                roots = await tools.list("/")
                print("挂载：", [item["path"] for item in roots.items])
                routes = await tools.list("/work")
                print("资料目录：", routes.items)

                page = await tools.query("/work/history", Query(limit=1))
                print("收件历史总数：", page.total)
                rows = list(page.items)
                while page.next_cursor is not None:
                    page = await tools.query("/work/history", Query(
                        limit=1, cursor=page.next_cursor,
                    ))
                    rows.extend(page.items)
                print("实际读得条数：", len(rows))

                path = "/work/content/" + str(rows[0]["id"])
                parts, offset = [], 0
                while True:
                    chunk = await tools.read(path, offset=offset, size=7)
                    parts.append(chunk.data)
                    if chunk.next_offset is None:
                        break
                    offset = chunk.next_offset
                print("第一条原文：", b"".join(parts).decode("utf-8"))
        finally:
            await information.close()


asyncio.run(main())
```

```bash
uv run python read_messages.py
```

预期看到 `/work` 已挂载，主体 `a` 的收件历史总数是 2，实际也读得 2 条。第一条原文里有发送者、`work` 和“第 1 轮完整消息”。`history` 的 SQL 授权按收件人过滤，把 scope 换成另一个主体，读到的就是那人自己的收件历史。

## 分页与分块读取的边界

示例故意每页只取一条消息。`total` 是符合授权条件的总数，`next_cursor` 指向下一批；读到最后一页，游标才为 `None`。

正文每 7 字节读一次，这个大小同样是故意挑的。一个中文字可能横跨两段，所以先把 bytes 拼起来再统一解码。示例读的是已经停止的运行，正文固定不动；遇到还在变化的资料，就要沿着返回的版本或 `expected_revision` 走，数据或权限一变就重新定位。版本与定位的完整说明见[信息与版本](../concepts/information.md)。

`Moment(2, "read")` 给出这次访问的业务语境。要读历史快照，得先准备[完整步骤只读视图](../concepts/complete-steps.md)；只把 Moment 设成过去的时间，数据库不会跟着倒回去。

## 模型眼里的文件空间

LLMDriver 把同一批资料投影成文件工具能发现的路径。模型从 `ls` 的根目录起步，再读具体文件；参数怎么写见[工具参考](../reference/tools.md)。

| 路径 | 用途 | 什么时候存在 |
| --- | --- | --- |
| `/world` | 当前授权环境资料 | 已登记 Information 提供者 |
| `/context` | 本次认知输入及扩展材料 | 对应激活上下文已准备 |
| `/workspace` | 主体私有笔记和工作文件 | 已装配持久工作区与 Shell |
| `/results` | 该主体登记的完整工具或命令产物 | 实际产生并登记产物后 |

Python 信息路径 `/work/history` 在文件空间里位于 `/world` 下面，目录投影会给出具体文件和续页入口。顺着返回的路径继续找，就不用猜文件名。私有工作区怎么装配、怎么写入，见[工作区指南](../guide/workspace.md)。

资料读取让主体取得已有内容。要发送新消息，需要调用环境登记的操作，下一课进入[发现与执行行动](actions.md)。
