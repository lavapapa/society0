# SQLInformation

`from society0.kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec` 将机制已维护的 SQL 表暴露为授权资料。服务借用 StageReader 或具备 read 的存储，调用者负责 reader 生命周期。

## 一、登记

登记定义可信查询范围，模型与主体通过 Query 提供参数化条件。

<!-- api: society0.kernel.information_sql.SQLInformation -->
<!-- api: society0.kernel.information_sql.DatasetSpec -->
<!-- api: society0.kernel.information_sql.DocumentSpec -->
<!-- api: society0.kernel.information_sql.SamplePage -->

routes 的键为非空单路径段。数据集要求单列 INTEGER 主键或显式非空 TEXT 主键，排序字段非空；保留字段 ref 不能作为业务投影字段登记。DocumentSpec 正文为 TEXT/BLOB，要求可定位原生 rowid 的普通表。注册时检查 schema，非法配置抛 `ValueError`。

authorize 返回 SQL 谓词与 bindings，表达行级可见范围。dependencies 列出权限、计数等附加表；自身数据表自动纳入。base_count 必须严格对应授权集合，并与事实在同一规范事务维护。附加 filters 存在时使用授权条件下的 COUNT，避免将基础总数用于筛选结果。

长正文通过 DatasetSpec.documents 映射到 DocumentSpec，要求同表、同主键、同正文列、同一 authorize 函数对象及同依赖集合。页项返回 document_ref，包括路径、完整长度、资源 Ref 和 expected_revision；正文 NULL 保留 None。正文列不能排序。字段说明、时间口径与 query_examples 用于 metadata 和 schema 发现，普通数据查询不扫描这些说明。

## 二、查询

每个请求用短读事务，正常分页使用确定的排序键继续读取。下面片段要求 `SQLInformation('reports', reader, routes)` 已挂载到 `/reports`，并登记含 id、price 字段的 orders 数据集。

<!-- api: society0.kernel.information_sql.SQLInformation.query -->
<!-- api: society0.kernel.information_sql.SQLInformation.metadata -->
<!-- api: society0.kernel.information_sql.SQLInformation.read -->

```python
from society0.kernel.interaction import Query

page = await session.information.query('/reports/orders', Query(
    fields=('id', 'price'), filters=(('price', 'le', 100),),
    order=(('id', 'asc'),), limit=100))
```

fields 与过滤字段来自登记范围；过滤支持 eq/ne/lt/le/gt/ge/in，eq/ne 的 None 使用 SQL 空值语义。order 未含主键时追加主键升序。原始 SQL 不接受主体输入。实际查询速度取决于授权前缀、排序方向及机制索引。

普通 Page.total 为精确授权筛选总数，next_cursor 继续完整范围。游标绑定运行、主体、Moment、路径、投影、条件、排序、授权谓词及相关表版本；相关更新后抛错，应重查第一页。无关 Thread 写入保持业务游标有效。页 revision 是依赖表版本，不能作为 scope.revision 的全库版本使用。

sample_seed 执行显式 reservoir 抽样：SamplePage.total 为样本条数，population_total 为授权总体，next_cursor 为空。成本为遍历 N 个候选键与保存 O(limit) 个键及选中投影；取得总体用普通分页。

## 三、正文与预算

返回范围控制读取量，完整原文始终有明确读取入口。

Query.max_bytes 约束紧凑 UTF-8 JSON 页响应，至少 512。普通页达到预算时保留总数和续页；单个轻项放不下时报错，调整投影或预算。抽样超预算要求减小样本或投影，不静默删样本。未声明为正文的巨大字段仍可能在单行内物化。

DocumentSpec.read 在同一短快照内授权并定位 rowid，再用原生 BLOB 增量读取。连续读取传入首段或引用的 expected_revision；正文或权限依赖变化会拒绝拼接，无关表写入保持有效。TEXT 主键路径段使用 URL 百分号编码。Python Information 使用挂载路径，例如 `/reports/manual/intro`；ActorFiles 与 Shell 的文件路径在其前加 `/world`，例如 `/world/reports/manual/intro`，正文引用的 logical_path 已包含该前缀。

DatasetSpec 合成 JSON 文件会首次物化整条记录并写临时文件，后续连续范围请求复用一个槽位；切换主体、路径或版本可能重新准备。SQLInformation.close 清理该临时文件；Information 挂载默认借用，资源拥有者应登记关闭。大独立正文宜使用 DocumentSpec。

见[信息指南](<../guide/information.md>)与[文件教程](<../tutorial/files.md>)；源码见 [SQLInformation](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/information_sql.py#L50>)。
