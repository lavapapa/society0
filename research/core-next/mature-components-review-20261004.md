# Society0 成熟组件选型复核

本次研究于 2026-10-04 核对现行代码和官方资料，将 Society0 的问题分解为已有成熟解法的工程问题，再确定应用应保留的语义。当前产品基线为 `89ad172`，研究工作树 HEAD 为 `0ae6f7d`。本轮没有修改产品、安装依赖或发出模型请求。以下结论决定下一轮试验顺序；候选的功能支持、实际部署通过和性能收益分别留证。

## 一、判断

当前需要减少的是协议、格式和生命周期的长期维护责任。引入一个底层库后继续自行管理它已经提供的压缩队列、网络连接或索引结构，仍然会累积大量自有复杂度。选择标准应包括能删除哪些代码、哪些异常处理由上游承担、完整工作负载是否变轻，以及是否保留仿真的真实语义。

代码盘点发现，`resource_managers.py` 约 2,400 行，混合提供方协议、重试、许可和嵌入合批；`_json_chunks.py` 自行调度压缩 future；Datasets 使用 SQLite 容器保存自定义压缩块布局；Observation 自行实现 HTTP 接纳、线程和 socket 生命周期。这些是优先重新选型的对象。SQLite/APSW、RapidJSON、Bashkit、graphlib 与 ExitStack 已经承担部分成熟基础能力，应继续按实际职责审查。行数代表盘点范围，并非预期删除数量。

详细证据分为[提供方研究](reuse-provider-study-20261004.md)、[存储研究](reuse-storage-study-20261004.md)和[执行与文件研究](reuse-runtime-study-20261004.md)。本文将这些调查与查询、限流、权限、多核、工作流、可观测性及测试工具合并为二十项问题目录。依赖的“最新”身份以报告日期为准；新功能仍须经过固定版本试验。

## 二、模型

模型交互包含协议适配、资源调度和主体决策三个层次。先替换变化频繁的协议，再评估现成框架能接管的发现与决策能力，可以减少同时改变多层行为造成的判断困难。

**01．多提供方协议适配。** 首选验证 Pydantic AI 的低层 Model/direct 与 EmbeddingModel，LiteLLM SDK 作为对照。前者在 Python 进程内提供类型化消息、流和嵌入，能够保留 Society0 对行动循环的控制；后者的 OpenAI 形状更接近现有消息字典，提供方覆盖广。pi-ai 的签名、增量工具参数和取消接口值得实测，但当前为 TypeScript 包，接入 Python 需计入长驻 Node 进程与全文 IPC；本轮未确认它具有统一 embedding 接口。aisuite 的已读主干存在 thinking 流丢弃和原 content 改写，优先级较低。替换目标是客户端分支、响应归一化和物理嵌入适配；保留完整 Thread、实际调用身份和领域行动约束。[Pydantic direct](https://pydantic.dev/docs/ai/core-concepts/direct/)、[LiteLLM](https://docs.litellm.ai/)、[pi-ai 固定版本](https://github.com/earendil-works/pi/tree/v1.0.2/packages/ai)。

**02．限流、重试与连接池。** 这是经典接纳控制问题。并发许可限制在途请求，RPM/TPM 限制单位时间用量，两者分别配置；轻量 RPM 可采用 aiolimiter，HTTP 连接复用由 SDK/HTTP 客户端负责，确有多部署需求时再比较 LiteLLM Router。明确一层重试所有者，记录每次实际请求，防止 SDK 与外层相乘重试。当前“取得许可后再物化完整历史”应保留为内存合同；普通 rate limiter 无法自动满足这一点。免费额度耗尽时等待或结束测试，不自动切换收费端点。[aiolimiter](https://aiolimiter.readthedocs.io/en/stable/)、[HTTPX 连接池](https://www.python-httpx.org/advanced/resource-limits/)、[LiteLLM Router](https://docs.litellm.ai/docs/routing)。

**03．动态行动发现。** 大行动空间属于目录发现、延迟装载与工具检索问题。Pydantic AI 当前官方文档已有 On-Demand Capabilities 和 ToolSearch，可延迟提供工具定义，并按提供方使用原生或本地搜索；MCP 也已有工具列举、分页和变更通知协议。它们应进入现有 meta tool 的对照范围。Society0 保留目标对象、主体资格、当前版本和执行时重验；工具发现算法、schema 运输与外部协议尽量复用。框架目录列出新能力尚不足以证明可脱离其 Agent 循环使用，须固定已发布版本核查接入边界。[按需能力](https://pydantic.dev/docs/ai/capabilities/on-demand/)、[ToolSearch](https://pydantic.dev/docs/ai/capabilities/tool-search/)、[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。

**04．决策驱动与上下文工作区。** LLM、规则、决策模型都可实现同一个主体驱动合同。Pydantic AI 当前提供 Decision models（含相关 Jev 接口）及 Workspaces，说明这些问题已有框架资产。可比较其低层模型或 workspace 协议；框架默认的自动重试、上下文处理和工具循环须逐项对照研究合同。主体主观状态、共享环境中的行动影响及仿真时间归 Society0；原生模型协议、通用文件工具和可复用执行组件交给候选库。本项属于扩展路线研究，尚无迁移或性能结论。[Decision models](https://pydantic.dev/docs/ai/models/decision/)、[Workspaces](https://pydantic.dev/docs/ai/core-concepts/workspace/)。

## 三、数据

存储应按访问模式选型。热状态、冷正文、分析表和向量各有成熟表示；主体看到文件目录，可以由这些表示投影得到，无需让所有权威数据都变成物理文件。

**05．事务、索引与变更捕获。** 热状态继续优先 SQLite/APSW。原生事务、索引、WAL、Session 和 backup 已覆盖大量责任。大行更新仍可能让 Session 保留旧行，流式输出无法消除捕获期间的内存；应通过热字段与正文分离缩小更新单位。插件用普通表和规范事务，避免再造行级日志、页缓存或通用 MVCC。SQLite 单写者与同机 WAL 是明确边界，多核计算结果可批量交给规范写者。[SQLite Session](https://www.sqlite.org/sessionintro.html)、[APSW Session](https://rogerbinns.github.io/apsw/session.html)、[SQLite WAL](https://www.sqlite.org/wal.html)。

**06．压缩与多核管线。** 当前冷数据已经用 zstd，Thread 等正文还使用自有分块队列加 zlib。顺序 changeset 优先试 python-zstandard 的 streaming 与原生 workers，让库管理压缩作业；待相同语义试验通过后删除相关 future 调度。原生窗口、工作缓冲和线程有实际内存，64KiB 小输入往往不足以发挥多线程。Python 3.14 已有 compression.zstd，项目 3.12 可继续成熟第三方绑定，无需仅为此升级解释器。[zstd 多线程](https://python-zstandard.readthedocs.io/en/latest/multithreaded.html)、[Python zstd](https://docs.python.org/3.14/library/compression.zstd.html)。

**07．压缩正文的随机读取。** 这是分帧压缩与可定位文件问题。优先比较 pyzstd 的 SeekableZstdFile，利用现成 seek table、二分定位和跨帧读取，替换 Dataset 自有 blocks 表及解码缓存；SQLite 可保留记录编号到正文 offset/length 的轻索引。普通 zstd 帧不自动提供快速随机定位。现成绑定的 seek table 内存随帧数增长；上游实现约两组 int64 偏移，64KiB 帧的 100GiB 正文仅该目录约 25MiB，另有解析临时空间和解压上下文。这是源码推算，必须测多文件并发打开的总成本。索引和正文分为两个工件后，还要一起进入完整点。[SeekableZstdFile](https://pyzstd.readthedocs.io/en/stable/pyzstd.html#seekablezstdfile-class)。

**08．不可变表与批量分析。** 对有 schema 的事实、结果和统计表，优先 Parquet+DuckDB；按列投影、过滤下推、压缩页和扫描由引擎完成。跨进程数值批次可用 Arrow IPC，避免反复转为 Python dict。任意 JSON 和巨型文本继续走正文接口；完整顺序显式保存 ordinal，精确类型需定义映射。迁移目标是删除自有数据扫描与聚合逻辑，避免增加一套跨格式查询引擎。[DuckDB Parquet](https://www.duckdb.org/docs/current/data/parquet/overview)、[Arrow IPC](https://arrow.apache.org/docs/python/ipc.html)。

**09．JSON 编解码。** 当前 RapidJSON 已承担原生遍历，应继续复用。orjson 适合特定小消息性能比较，但整体 bytes 输出、整数范围及特殊浮点处理与现行合同有差异。SQLite JSONB 可降低 SQL JSON 解析成本，多数操作仍为 O(N)。正文压缩率高说明重复信息多，无法推出 Python 对象内存小；实际内存还包括 dict、字符串对象、指针、解析中间副本。改进重点是按需物化、类型化批次与小行更新。[RapidJSON](https://python-rapidjson.readthedocs.io/en/latest/dump.html)、[orjson](https://github.com/ijl/orjson)、[SQLite JSONB](https://www.sqlite.org/json1.html)。

**10．查询生成与游标分页。** SQLAlchemy Core 和 sqlakeyset 已覆盖查询表达式与 keyset 分页；应先比较是否能删除当前 SQLInformation 的通用拼装代码。sqlakeyset 依赖 SQLAlchemy Connection/Session，当前 APSW Session 连接有既定所有权，接入成本可能超过小型适配收益。权限范围、固定版本、精确 total 和原文引用仍需明确合同。继续使用 SQL 原生索引，严禁为了适配引入第二权威连接或全表 Python 过滤。[SQLAlchemy Core](https://docs.sqlalchemy.org/en/21/core/)、[sqlakeyset API](https://sqlakeyset.readthedocs.io/en/latest/api.html)。

**11．完整点、备份与分叉。** 这里包含数据库备份、应用发布和持久工作流三种经典问题。SQLite backup、Litestream、RocksDB Checkpoint、Temporal/DBOS 都值得比较，但覆盖范围各异。先重审能否以完整步事务简化当前 live/complete 两层协议；若需要运行中短事务可见，则必须保留某种明确的完整点协调。RocksDB 的 SST 硬链接 checkpoint 有利于低复制分叉，代价是重设关系表、查询与绑定；Litestream 适合数据库备份；持久工作流适合长任务恢复。DBOS 当前架构使用 PostgreSQL 保存工作流状态，SQLite datasource 文档不足以证明其系统数据库可直接用 SQLite。选型须覆盖权威状态与外部正文的一致恢复，通用工作流不能直接推导出所有副作用恰好一次。[SQLite backup](https://www.sqlite.org/backup.html)、[Litestream](https://litestream.io/how-it-works/)、[RocksDB Checkpoints](https://github.com/facebook/rocksdb/wiki/Checkpoints)、[DBOS 架构](https://docs.dbos.dev/architecture)、[Temporal](https://docs.temporal.io/workflow-execution)。

Litestream 当前 LTX 一个 TXID 可覆盖多个 SQLite 事务，压实和清理还会改变可保留的恢复粒度。任意完整步骤恢复需要实际封存边界与保留策略支持，单独记录 step→TXID 不足以成立。此条件已纳入存储专题的独立复核。

**12．向量索引与记忆检索。** 保留记忆原文及原始向量为权威数据，以已保存向量比较 Chroma 与 sqlite-vec，整个比较无需新的 embedding 费用。前者已接入，后者可降低独立检索组件的部署成本；精确扫描随候选量和维度增长，向量虚表也不能直接纳入普通 Session 假设。Qdrant Server 适合明确的大库或跨进程共享需求，Edge 当前仍为 beta，分别评价成熟度。候选集合、距离、最终重排会影响主体认知，应验证有效结果及恢复，不能以“同为向量库”推断语义相同。[sqlite-vec](https://alexgarcia.xyz/sqlite-vec/)、[Qdrant storage](https://qdrant.tech/documentation/manage-data/storage/)、[Qdrant Edge](https://qdrant.tech/documentation/edge/)。

## 四、执行

执行层的复用重点是任务所有权、资源生命周期和成熟解释器。领域规则决定因果关系，通用组件负责把既定关系可靠地运行起来。

**13．任务生命周期与邮箱。** 优先用标准库 TaskGroup 收敛任务集合、失败取消和等待收尾；复杂清理再比较 AnyIO CancelScope。当前按主体信号合并、同主体串行和 phase 完成条件保留为显式邮箱语义。TaskGroup 对子任务 CancelledError 有例外，行动取消后的步骤失效仍需明确登记。还需减少“已创建但等待 semaphore 的 task”数量，让内存由实际活动任务和轻量待办决定。AnyIO 的取消行为与 asyncio 有差异，应以完整 phase 验证双重取消和清理失败，避免零散包裹。[TaskGroup](https://docs.python.org/3.12/library/asyncio-task.html#task-groups)、[AnyIO cancellation](https://anyio.readthedocs.io/en/stable/cancellation.html)。

**14．插件装配与离散事件。** 当前依赖排序已用 graphlib，资源退出已用 ExitStack，继续保留；外部分发可用 importlib.metadata entry points，复杂 hook 生态再考虑 pluggy。时间调度中的等待、资源争用和到期事件可采用 SimPy，阶段式共享状态调度维持明确顺序。SimPy 模拟并发与 CPU 并行不同，运行中的 generator 也不能直接当持久检查点；插件需保存可重建的领域状态与待到期事实。[graphlib](https://docs.python.org/3/library/graphlib.html)、[pluggy](https://pluggy.readthedocs.io/en/stable/)、[SimPy](https://simpy.readthedocs.io/en/latest/topical_guides/time_and_scheduling.html)。

**15．虚拟文件系统与 shell。** Bashkit 已承担 shell、命令、管道与 OverlayFs，优先缩减自有 Rust/Python 桥，并用 just-bash/bash-tool 作真正替代对照。fsspec 适合将来的远端文件后端；OpenViking 适合作为知识资料与分层检索的参考或服务。当前 Workspace read 回调仍会整文件返回 bytes，必须纳入大文件试验；“懒加载”无法证明 head/tail 的读取量受请求范围约束。共享世界以按主体授权的视图挂载，私有文件保存变更，避免每主体复制 World。文件命令、版本与内容的桥接应保持很薄。[Bashkit](https://bashkit.sh/api/python/)、[just-bash](https://github.com/vercel-labs/just-bash)、[fsspec](https://filesystem-spec.readthedocs.io/en/latest/features.html)、[OpenViking](https://github.com/volcengine/OpenViking)。

**16．角色与访问策略。** 这是 ACL/RBAC/ABAC 的应用变体。Casbin/PyCasbin 可承担多角色、属性规则与策略求值；简单 owner/actor 条件继续由 SQL 查询表达，避免逐行调用 Python 策略器造成全表扫描。当前阶段保留小型 Access 接口，复杂插件出现共享策略语言需求时比较 Casbin。权限策略必须对应仿真版本；查询可见集合、目录发现和执行资格分别验证，库不自动提供 SQL 下推或历史策略快照。[Casbin 模型](https://casbin.apache.org/docs/supported-models/)、[ABAC](https://casbin.apache.org/docs/abac/)。

**17．多核与分布式任务。** 先用成熟原生批处理：zstd workers、DuckDB 查询、Arrow/NumPy 数值批次；纯 Python 且足够大的独立计算使用 ProcessPoolExecutor。Ray/Dask 可在真实跨进程任务图、对象共享或分布式需求出现时接管执行。批次传紧凑输入或不可变引用，结果集中提交，避免传整 World。Ray 对部分数组可零复制，普通 Python 对象仍有序列化成本；每个逻辑主体一个进程或常驻重量级 actor 会放大内存。统一并发预算还要防止各库各开全部核心造成超额争用。[Ray serialization](https://docs.ray.io/en/latest/ray-core/objects/serialization.html)、[Dask efficiency](https://distributed.dask.org/en/stable/efficiency.html)。

## 五、接口

运行中观察与运行后分析共享权威数据，但传输、诊断和测试均有成熟基础设施。此处有直接删除底层代码的机会。

**18．HTTP、RPC 与实时订阅。** 优先 Starlette+Uvicorn 替换当前手工 HTTPServer 接纳、线程、socket 和关闭逻辑；正式 OpenAPI 需求再引 FastAPI。保留查询服务层，统一 status、固定版本分页与追加 tail。SSE/WebSocket 可承担通知传输，断线后从持久序号继续读取；界面状态可合并，完整事实仍可补读。Uvicorn 的并发数、keep-alive 和应用 body 超时有不同含义，替换需验证慢读写、断开、退出和数据库连接线程归属。[Uvicorn server behavior](https://uvicorn.dev/server-behavior/)、[Starlette lifespan](https://starlette.dev/lifespan/)。

**19．性能观测。** 使用 OpenTelemetry 的 span/metric 接口对接可选导出器，减少自有计时分发、上下文关联和监控协议。actor、step 等高基数身份放在 span 或权威查询记录；metric 默认使用阶段类别、状态等低基数标签，计耗时、队列、字节和内存。短字符串仍可能形成随主体数乘历史增长的时间序列，需验证 SDK 基数配置和导出队列。完整 Thread、业务事实和原始模型响应仍进入运行工件，避免 telemetry 再复制一份巨大正文。没有监控消费者时采用空导出或既有轻量记录，库层依赖 API 即可。[OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/)、[metric 基数限制](https://opentelemetry.io/docs/specs/otel/metrics/sdk/#cardinality-limits)。

**20．故障组合与测试生成。** pytest 配合 Hypothesis 状态机测试，可生成写入、取消、关闭、恢复、再读的组合并缩减失败序列；HTTP 传输替身和固定协议样本覆盖 429、流中断、签名续接与重试。复杂测试不依赖收费 LLM。真实模型验证留给免费端点的工具往返、实际参数和记忆链；完整能力与性能仍用确定性数据对照。每项替换既验库适配，也验外部消费者和恢复，不以测试数量代替风险判断。[Hypothesis stateful](https://hypothesis.readthedocs.io/en/latest/stateful.html)。

## 六、推进

下一轮按维护责任可删除程度安排试验，保留现行代码作比较基线。所有选型均有小型结束条件，选定后删除被替代实现，随后再做全量集成。

第一批处理模型协议、压缩和 HTTP 三个清楚边界：Pydantic AI 低层与 LiteLLM SDK 用相同离线协议样本比较；原生 zstd streaming 与 seekable 正文用既有真实大根比较；ASGI 用当前外部接口场景比较。每个结果须说明删除范围、保留适配和新依赖成本，达到同一合同后进入正式替换。

第二批处理结构化并发、表型冷数据和向量检索：TaskGroup 优先、AnyIO 对照；Parquet+DuckDB 使用实际有 schema 的消费者；Chroma/sqlite-vec 使用已有向量。VFS 原生范围读取和动态 action 发现另设短验证，避免把整文件物化成本藏在 callback 中。

第三批仅在对应需求成立时引入重型方案：频繁低复制分叉比较 RocksDB，跨机器任务图比较 Ray/Dask，独立知识库比较 OpenViking，长任务恢复比较持久工作流。当前 Python Core、原生 C/Rust 热路径与 TypeScript 工作台的语言安排继续作为试验起点；整库换语言要由删减适配和整步资源证据支持。

测试费用约束即时生效：DeepSeek 不再用于测试；其他服务须确认具体账户与模型免费才发请求。Gemini/Groq 官方存在免费档，但已持有密钥不证明当前调用免费。离线回放、已保存向量与 local-only 模型先行，达到免费限额时等待或结束，无收费 fallback。[Gemini 定价](https://ai.google.dev/gemini-api/docs/pricing)、[Groq 限额](https://console.groq.com/docs/rate-limits)。

实施 TODO 新增 R01–R04；本轮完成问题盘点和官方候选研究，原型、替换、全量验收仍待执行。核心判断是：Society0 应长期维护共享环境、主体认知与行动、机制因果和完整步骤的合同；网络协议、压缩文件、数据库内部机制、shell 与通用任务生命周期尽量由成熟组件承担。每次试验以可删除责任和相同工作负载的真实代价作为结论依据。
