# SQL 信息提供者

SQLInformation 把机制已经维护的当前表、历史表和文档暴露为共享信息视图。机制声明表、可见字段、授权谓词和索引，Core 将有限查询请求转为参数绑定 SQL。每次请求使用 StageReader 的短只读事务，响应携带实际 live_revision。

## 一、注册

`SQLInformation(namespace,reader,routes,max_page_size=1000)` 创建提供者，由 Information 挂载到对应绝对 namespace 前缀。routes 是少量集合配置，不按主体或每条记录建立目录。

```python
provider = SQLInformation('world', StageReader(run_path), {
    'orders': DatasetSpec('orders', 'id', ('id','price','status'),
        authorize=lambda scope: ('owner=? AND active=1', (scope.actor,)),
        order_fields=('id',),
        base_count=lambda scope: ('SELECT total FROM counts WHERE owner=?', (scope.actor,))),
    'documents': DocumentSpec('documents','id','body'),
})
information.mount('/world', provider)
```

DatasetSpec 的 table、key、columns、authorize、order_fields、base_count 都由可信机制注册。主键为单列 INTEGER 或显式非空 TEXT，字段从 SQLite schema 核对；ref 是响应保留字段。允许排序的列要求非空，首版遇到可空排序列直接拒绝注册，避免分页跳过 NULL。

DocumentSpec 指定单个 BLOB 正文列以及可选授权谓词。它使用普通 rowid 表，WITHOUT ROWID 与三个 rowid 别名均被业务列遮蔽的布局明确拒绝。SQLite 名字不区分大小写，隐藏 rowid 别名选择也按这一规则处理。机制应把正文作为不可变 BLOB 记录保存；更新正文会推进 current revision，跨请求固定读取需要使用版本合同。

## 二、请求

`/world` 列出注册集合，`/world/orders` 查询记录，`/world/documents/<key>` 按主键定位文档。TEXT 主键的路径段使用 URL 百分号编码。记录页给每项附 `Ref(namespace,route,str(key))`，主体可直接据此发现与执行动态动作。

Query 支持 fields、filters、order、limit、cursor、sample_seed。过滤运算为 eq、ne、lt、le、gt、ge、in；eq/ne 的空值使用 SQL IS NULL/IS NOT NULL。字段来自注册表，值使用 SQL bindings。查询不接受主体提供的原始 SQL。order 是 `(field,'asc'|'desc')` 序列，缺少主键时自动追加主键升序以保持确定顺序。

正常分页使用最后一行的排序键做 keyset 继续读取。词典序后继拆成互斥的索引范围，每个范围最多取 limit+1 行，由 SQLite 原生 UNION ALL 按相同排序合并有界候选；避免简单 OR 谓词在相同排序值的大前缀内重扫。实际速度仍要求机制建立匹配授权前缀与排序方向的索引。cursor 可经 JSON 往返，绑定 actor、Moment、路径、字段、过滤、排序、抽样配置、实际授权谓词及 bindings、数据库 revision。后续页版本变化会明确拒绝，调用方重新查询；一个固定 revision 的多页读取不占用长期数据库读事务。

无额外 filters 时，可使用插件同步维护的 base_count。其计数必须严格对应 authorize 授权集合，机制同一次权威事务修改事实、活动索引及计数。用户增加任何 filters 后改用对应授权 SQL COUNT，避免把基础总数误当筛选总数。COUNT 和排序的工作量依赖实际过滤、索引和 SQLite 查询计划，Core 不宣称所有查询恒定成本。

sample_seed 表示一次显式分析抽样。实现按主键流式遍历授权候选键，用固定随机种子做 reservoir sampling，再一次查询被选行的投影；不会为抽样逐条物化大正文。SamplePage.total 是实际样本条数，population_total 是授权总体条数，next_cursor 为空。完整总体仍通过不带 sample_seed 的普通分页取得。抽样读取 O(N) 个候选键，驻留 O(limit) 个键及选中投影，属于显式分析成本。

## 三、正文

read 先在同一只读事务内用主键与授权条件定位真实 rowid，再调用 ReadView.read_blob 的原生 SQLite 增量 BLOB 读取。响应为 DocumentChunk，保留原始 bytes、total_bytes、next_offset、revision 和 source。SQL substr(BLOB) 在本项目实测中仍产生整 BLOB 临时副本，因此本实现不依赖 substr 达成内存边界。

read 的工作量由范围所需的页和连接缓存决定。提供者不先构造全部文档；shell 的文本与 base64 展示遵循独立 shell 合同。默认 current 请求返回当时版本；需要固定内容的消费者可传带 revision 的 InteractionScope，版本变化时拒绝继续。权限与正文定位处于同一 SQLite 快照内，避免先授权一行后读到另一行。

## 四、组合

可执行示例位于 `examples/core_next/shared_environment.py`。它创建真实 StageStore、PluginHost、Runtime 和两个机制：交易动作在同一规范事务更新订单、活动计数及消息；两个规则主体按默认串行阶段运行。主体经 shell 读取制度原文、抽样历史、在私有 workspace 用 jq 分析价格，发现与执行购买动作，接收方随后读取自己的消息。

```bash
uv run --extra shell python examples/core_next/shared_environment.py /tmp/society0-shared-demo --history 2000
```

运行目录必须是本次拥有的新目录。示例通过 Runtime.prepare_artifact 封存完整 stdout、stderr、动作 receipt 与私有 workspace snapshot，用 SQL analysis 表保存原引用到不可变工件的映射。完整步骤恢复后逐值比较全部订单与消息，验证所有结果工件存在，并用新 scope 恢复 workspace 中的分析值。示例使用规则 Driver，不包含真实 LLM 或向量记忆验收。

## 五、证据

作者测试在 `tests/primary/test_kernel_information_sql.py`，存储作者的非作者测试在 `test_kernel_information_sql_review.py`。原始输出位于 `research/core-next/information-sql-*.txt`。检查范围包括授权总数、额外过滤、混合升降及相同排序值、JSON 游标、版本变化、只读取样本键、真实 BLOB 范围、rowid 大小写别名以及完整示例恢复。

匹配索引的实验保留了反例：1000 与 10000 条历史、固定 5 条页，缺 owner/id 索引时 SQLite VM 指令从 3547 增至 35047；机制补上索引并使用维护计数后，两档均为 58 条指令，读取 7 个短元数据行、56 字节已物化字段。日志在 `information-sql-index-red.txt` 和 `information-sql-workload.txt`。另有同值排序前缀深页反例，浅页位置 100 与深页位置 9000 的简单 OR 实现增长到 873/72073 条 VM 指令；最终范围合并实现通过同向与混合方向的深页计数验收，原始日志以 information-sql-review-seek 与 information-sql-mixed-seek 开头。这证明该具体当前查询路径的工作量，不代表任意过滤或任意业务 schema 都达到相同成本。

非作者实验另以 32 MiB BLOB 请求 64 字节，先复现 substr 导致约 33.8 MB 原生 SQLite 临时内存，再验证原生增量读取低于测试的 8 MiB 上界。该测量覆盖 SQLite 原生内存，而不仅是 Python 返回值大小；完整 World、模型上下文及任意大单条投影的内存边界仍需各自验收。
