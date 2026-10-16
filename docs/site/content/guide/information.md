# 向主体提供数据与原文

主体要看插件存下来的事实，就得有人把这些表接到信息入口上：`SQLInformation` 把同一个 StageStore 里的表映射成可查询的数据集和可按字节读取的文件。下面接着[插件教程](plugins.md)往下做：先建表并写入初始资料，安装插件时登记信息提供者，再让规则或模型主体把内容读出来。

## 在插件里建表并登记资料

在仓库根目录创建 `study_information.py`，把下面的完整插件定义存进去。例子让 Alice 读自己收到的两份报告：标题适合直接查询，正文通过文件引用读取。

```python title="study_information.py"
from society0.kernel.plugins import Plugin
from society0.kernel.storage import StageReader
from society0.kernel.information_sql import (
    DatasetSpec, DocumentSpec, SQLInformation,
)

SCHEMA = (
    "CREATE TABLE study_reports("
    "id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, "
    "title TEXT NOT NULL, body BLOB NOT NULL)",
    "CREATE INDEX study_reports_recipient ON study_reports(recipient,id)",
)


def initialize(writer):
    writer.executemany(
        "INSERT INTO study_reports VALUES(?,?,?,?)",
        [(1, "alice", "市场周报", "本周成交量上升。".encode("utf-8")),
         (2, "alice", "来源说明", "资料来自本实验的初始设定。".encode("utf-8"))],
    )


def reports_plugin():
    def install(context):
        store = context.require("storage", "store")
        reader = StageReader(store.path)
        context.on_close(reader.close)
        recipient = lambda scope: ("recipient=?", (scope.actor,))
        provider = SQLInformation("reports", reader, {
            "items": DatasetSpec(
                "study_reports", "id", ("id", "title", "body"),
                authorize=recipient,
                documents=(("body", "text"),),
                field_descriptions={"title": "报告标题", "body": "报告原文引用"},
            ),
            "text": DocumentSpec(
                "study_reports", "id", "body", authorize=recipient,
            ),
        })
        context.on_close(provider.close)
        dispose = context.require("interaction", "information").mount("/reports", provider)
        context.on_close(dispose)
        context.provide("information", provider)

    return Plugin(
        "reports", requires=("storage", "interaction"), install=install,
        schema=SCHEMA, initialize=initialize,
    )
```

在[自己的运行计划](../tutorial/first-plan.md)里加上 `from study_information import reports_plugin`，再把 `reports_plugin()` 放进 `plugins` 列表。计划原有的 `interaction_plugin(...)`、Actor、Runtime 和 Schedule 继续负责主体激活。新运行会写入这两份报告；从完整步骤恢复时直接沿用已经保存的资料。

`DatasetSpec` 的前三项是表名、主键列和可见列，`DocumentSpec` 的前三项是表名、主键列和正文列。被映射的表都要有单一 INTEGER 或非空 TEXT 主键，正文列用 TEXT 或 BLOB。例子里两个声明共用同一个 `recipient` 函数；用 `documents` 把正文关联进来时，两边的表、主键、正文、授权函数和依赖必须一致。

## 让规则主体分页读取正文

把下面这个函数换进自己的规则计划，主体 ID 保持 `alice`。查询走 Information 的路由路径 `/reports/items`；文件工具里显示的逻辑路径还要带 `/world` 前缀。

```python
from society0.kernel.interaction import Query
from society0.kernel.runtime import DriverResult


async def rule(session):
    titles = []
    cursor = None
    while True:
        page = await session.information.query(
            "/reports/items", Query(fields=("id", "title", "body"),
                                    limit=1, cursor=cursor),
        )
        for item in page.items:
            document = item["body"]
            raw = bytearray()
            offset = 0
            while True:
                part = await session.information.read(
                    document["path"], offset=offset, size=4096,
                    expected_revision=document["expected_revision"],
                )
                raw.extend(part.data)
                if part.next_offset is None:
                    break
                offset = part.next_offset
            titles.append({"title": item["title"],
                           "text": raw.decode("utf-8")})
        cursor = page.next_cursor
        if cursor is None:
            break
    return DriverResult("completed", {"reports": titles})
```

返回的 `Page` 里有 `items`、完整的 `total`、`next_cursor` 和 `revision`。映射成正文字段的列返回的是引用，含 `path`、`logical_path`、`total_bytes`、`expected_revision` 等信息；上面这段代码按字节拼接再解码，跨段的中文不会断。阶段原有的 `StepResult` 可以继续保存这个决定值，读取方式见[结果教程](../tutorial/results.md)。

LLM 文件工具可以先 `ls` `/world/reports/`，再读 `/world/reports/text/1`。主体能发现哪些路径，同时受 Interaction 授权和 SQL 行条件限制；换一位没有对应 recipient 记录的主体，查询返回的集合也会跟着变。

## 核验授权与续读

用自己的规则工厂初始化一次新运行，读决定表里的 `reports`：Alice 应该拿到两份报告的标题和完整中文原文，Bob 应该拿到空列表。把分页 limit 保持为 1，能顺带检查第二页没有遗漏；把正文 size 改成很小的字节预算，就能检查跨中文字符的续读拼接。

例子里创建 reader 之后马上登记关闭，接着登记 provider 和路由的注销。退出或安装失败时，先撤回路由，再关闭 provider 和 reader。路由使用默认的借用方式，所有权留在 reports 插件。

## 把查询范围和索引设计好

查询只能看到声明公开的列，过滤和排序同样以提供者登记的字段为准。要按非主键字段排序，先把这些字段写进 `order_fields`，而且它们需要非空。授权函数返回 SQL 条件和绑定参数，条件里的动态值一律走参数绑定。索引要配得上授权条件和常用查询，免得主体每查一次都遍历完整历史。

资料目录和完整字段说明见[信息参考](../reference/information.md)与[交互参考](../reference/interaction.md)。已经有大批不再改动的资料时，可以用共享的 Datasets 服务封存，再由领域插件维护当前引用，完整例子见[不可变数据集](datasets.md)。选哪种方式，取决于资料会不会随业务变化，以及主体要靠什么定位原文。授权函数还要读别的表时，把这些表加进相应依赖声明，权限一变旧页才会过期；同一查询的续页保持相同的运行、主体、时点和版本。遇到过期错误就从新查询重新开始，不要把两个版本的记录拼在一起。
