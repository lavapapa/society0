# 从目录读到原文

上一课留下了真实对话消息。这一课以主体 `a` 的身份发现资料、查询消息目录，再分块读完一条中文正文。先完成[轮次对话](conversation.md)，保留 `runs/conversation/run`。

## 一、发现可以读取的资料

保存下面完整程序为 `read_messages.py`。它在已停止的示例运行上建立只读信息路由；这是演示身份绑定与资料访问的读取程序，运行中的 Driver 会直接取得 `session.information`。

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

预期看到 `/work` 挂载、主体 `a` 的收件历史总数 2、实际读得 2 条。第一条原文包含发送者、`work` 和“第 1 轮完整消息”。`history` 的 SQL 授权按收件人过滤；切换 scope 主体后，读取到该主体自己的收件历史。

## 二、分页和字节有什么作用

示例故意每页取一条消息。`total` 表示符合授权条件的总数，`next_cursor` 指向后续页。读取到最后一页，游标才为 `None`。

正文故意每次读取 7 字节。中文字符可能跨越两段，因此先拼接 bytes，再统一解码。示例在已停止的运行上读固定正文；对正在变化的资料，需要沿用返回的版本或 `expected_revision`，数据或权限变化后重新定位。完整合同见[信息与版本](../concepts/information.md)。

`Moment(2, "read")` 提供本次访问的业务语境。读取历史快照需准备[完整点只读视图](../concepts/complete-steps.md)，设置一个过去的 Moment 本身不会把数据库倒回过去。

## 三、模型看到的文件空间

LLMDriver 把相同资料投影成文件工具可发现的路径。模型可以从 `ls` 的根目录开始，再读取具体文件；实际参数见[工具参考](../reference/tools.md)。

| 路径 | 用途 | 什么时候存在 |
| --- | --- | --- |
| `/world` | 当前授权环境资料 | 已登记 Information 提供者 |
| `/context` | 本次认知输入及扩展材料 | 对应激活上下文已准备 |
| `/workspace` | 主体私有笔记和工作文件 | 已装配持久工作区与 Shell |
| `/results` | 该主体登记的完整工具或命令产物 | 实际产生并登记产物后 |

Python 信息路径 `/work/history` 在文件空间中位于 `/world` 下；目录投影会给出具体文件与续页入口。使用返回的路径继续发现，可避免猜测不存在的文件名。私有工作区的装配与写入见[工作区指南](../guide/workspace.md)。

资料读取让主体取得已有内容。要发送新消息，需要调用环境登记的操作，下一课进入[发现与执行行动](actions.md)。
