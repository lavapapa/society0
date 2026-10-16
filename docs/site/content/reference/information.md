# SQLInformation 与资料登记

`from society0.kernel.information_sql import SQLInformation, DatasetSpec, DocumentSpec` 把机制已经写入 SQL 表的业务数据登记成主体可查的资料，登记时说明路由、可见范围、字段投影和长正文位置，之后由 `SQLInformation` 在短读事务里按授权条件取数。服务只借用 StageReader 或具备 read 的存储，reader 由调用者负责；挂到 Information 上默认也是借用，资源拥有者应登记关闭。

## 登记查询范围

登记决定哪些数据可查、按什么条件判断可见，模型和主体在调用时通过 Query 提供参数化条件。

<!-- api: society0.kernel.information_sql.SQLInformation -->
<!-- api: society0.kernel.information_sql.DatasetSpec -->
<!-- api: society0.kernel.information_sql.DocumentSpec -->
<!-- api: society0.kernel.information_sql.SamplePage -->

routes 的键必须是非空单路径段。数据集要求主键为单列 INTEGER 或显式非空 TEXT，排序字段非空；保留字段 ref 不能登记为业务投影字段。DocumentSpec 的正文列为 TEXT/BLOB，所在表必须是可定位原生 rowid 的普通表。注册时检查 schema，配置不合法抛 `ValueError`。

authorize 返回 SQL 谓词与 bindings，表达逐行可见范围。dependencies 列出权限表、计数表这类附加表，数据表自身自动纳入。base_count 必须与授权集合严格对应，并与事实在同一规范事务维护。带附加 filters 时，总数用授权条件下的 COUNT 计算，不能把基础总数用在筛选结果上。

长正文通过 DatasetSpec.documents 映射到 DocumentSpec，两者必须同表、同主键、同正文列，用同一个 authorize 函数对象，依赖集合相同。页项返回 document_ref，含路径、完整长度、资源 Ref 和 expected_revision；正文为 NULL 时保留 None。正文列不能排序。字段说明、时间口径与 query_examples 用于 metadata 和 schema 发现，普通查询不扫描这些说明。

## 查询与翻页

每个请求用一次短读事务，翻页按确定的排序键继续。下面代码要求 `SQLInformation('reports', reader, routes)` 已挂载到 `/reports`，并登记含 id、price 字段的 orders 数据集。

<!-- api: society0.kernel.information_sql.SQLInformation.query -->
<!-- api: society0.kernel.information_sql.SQLInformation.metadata -->
<!-- api: society0.kernel.information_sql.SQLInformation.read -->

```python
from society0.kernel.interaction import Query

page = await session.information.query('/reports/orders', Query(
    fields=('id', 'price'), filters=(('price', 'le', 100),),
    order=(('id', 'asc'),), limit=100))
```

fields 与过滤字段都来自登记范围；过滤支持 eq/ne/lt/le/gt/ge/in，eq、ne 的 None 按 SQL 空值语义处理。order 未含主键时追加主键升序。原始 SQL 不接受主体输入。查询速度取决于授权前缀、排序方向和机制索引。

普通 Page.total 为授权筛选后的精确总数，next_cursor 继续完整范围。游标绑定运行、主体、Moment、路径、投影、条件、排序、授权谓词和相关表版本；相关更新后抛错，应从第一页重查。无关 Thread 写入不让业务游标失效。页 revision 是依赖表版本，不能当作 scope.revision 的全库版本使用。

sample_seed 执行显式 reservoir 抽样：SamplePage.total 为样本条数，population_total 为授权总体，next_cursor 为空。开销为遍历 N 个候选键、保存 O(limit) 个键与选中投影；取得总体用普通分页。

## 正文读取与返回预算

返回范围限制一次取多少内容，完整原文始终有明确读取入口。

Query.max_bytes 约束紧凑 UTF-8 JSON 页响应，至少 512。普通页达到预算时保留总数和续页；单个轻项放不下时抛错，需调整投影或预算。抽样超预算要减小样本或投影，不静默删样本。未声明为正文的巨大字段仍可能在单行内物化。

DocumentSpec.read 在同一短快照内授权并定位 rowid，再用原生 BLOB 增量读取。连续读取传入首段或引用的 expected_revision；正文或权限依赖变化会拒绝拼接，无关表写入不受影响。TEXT 主键路径段使用 URL 百分号编码。Python Information 用挂载路径，例如 `/reports/manual/intro`；ActorFiles 与 Shell 的路径要加 `/world` 前缀，例如 `/world/reports/manual/intro`，正文引用的 logical_path 已包含该前缀。

DatasetSpec 合成的 JSON 文件首次请求物化整条记录并写临时文件，后续连续范围请求复用一个槽位；切换主体、路径或版本时可能重新准备。SQLInformation.close 清理该临时文件；资源拥有者应登记关闭。大而独立的正文宜用 DocumentSpec。

见[信息指南](<../guide/information.md>)与[文件教程](<../tutorial/files.md>)；源码见 [SQLInformation](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/information_sql.py#L50>)。
