# 向主体提供数据与原文

领域插件保存的事实需要通过信息入口进入主体视野。`SQLInformation` 可以把同一 StageStore 中的表映射成可查询数据集和可分块读取的文件。本页接续[插件教程](plugins.md)：先声明表与初始资料，再在插件安装时登记信息提供者，最后让规则或模型主体读取实际内容。

## 一、在插件中登记资料

在仓库根目录创建 `study_information.py`，保存以下完整插件定义。例子让 Alice 读取自己收到的两份报告；标题适合查询，正文通过文件引用读取。

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

将 `from study_information import reports_plugin` 加入[自己的运行计划](../tutorial/first-plan.md)，再把 `reports_plugin()` 加入 `plugins` 列表。计划原有的 `interaction_plugin(...)`、Actor、Runtime 与 Schedule 继续负责主体激活。新运行初始化报告；恢复完整步骤时沿用已保存资料。

`DatasetSpec` 的前三项是表名、主键列与可见列；`DocumentSpec` 的前三项是表名、主键列与正文列。每张映射表需要单一 INTEGER 或非空 TEXT 主键，正文列使用 TEXT 或 BLOB。这里两个声明复用同一个 `recipient` 函数；通过 `documents` 关联正文时，它们的表、主键、正文、授权函数及依赖必须一致。

## 二、在规则主体中读取

把下面函数替换到自己的规则计划中，主体 ID 保持 `alice`。查询路径是 Information 路由路径 `/reports/items`；文件工具显示的逻辑路径另外带 `/world` 前缀。

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

返回的 `Page` 包含 `items`、完整 `total`、`next_cursor` 与 `revision`。正文映射字段返回引用，包含 `path`、`logical_path`、`total_bytes`、`expected_revision` 等信息；上述代码按字节拼接后解码，保留跨段中文。阶段原有的 `StepResult` 可以继续保存这个决定值，读取方式见[结果教程](../tutorial/results.md)。

LLM 文件工具可以先 `ls` `/world/reports/`，再读取 `/world/reports/text/1`。主体能发现的路径同时受 Interaction 授权和 SQL 行条件约束；新主体若没有相应 recipient 记录，查询返回的集合也会随之变化。

## 三、核验可见内容

用自己的规则工厂初始化新运行，读取决定表中的 `reports`：Alice 应取得两份报告的标题和完整中文原文，Bob 应取得空列表。将分页 limit 保持为 1，可同时检查第二页没有遗漏；把正文 size 改成很小的字节预算，可检查跨中文字符的续读拼接。

本例在创建 reader 后立即登记关闭，再登记 provider 和路由注销。退出或安装失败时先撤回路由，然后关闭提供者与读取器。路由采用默认借用模式，所有权留在 reports 插件。

## 四、设计查询范围

查询只公开声明的列，过滤与排序也按提供者的字段合同执行。需要按非主键字段排序时，在 `order_fields` 登记可排序字段；这些字段需要非空。授权函数返回 SQL 条件与绑定参数，条件中的动态值通过参数绑定传入。索引应与授权及常用查询相配，避免每次主体查询都遍历完整历史。

资料目录和完整字段说明见[信息参考](../reference/information.md)与[交互参考](../reference/interaction.md)。已有不可变大批资料可使用共享 Datasets 服务封存，再由领域插件维护当前引用；其完整例子见[不可变数据集](datasets.md)。选择哪种方式取决于资料是否随业务变化，以及主体需要怎样定位原文。授权函数读取额外表时，将这些表加入相应依赖声明，让权限变化能够使旧页过期；同一查询的续页保持相同运行、主体、时点和版本。遇到过期错误从新查询开始，避免拼接两个版本的记录。
