# 运行基础设施收敛选型

本报告把此前候选清单收敛为可执行的设计选择。研究日期为 2026-10-04，源码计数身份见同目录 `runtime-selection/code-size.json`；本次仅新增隔离实验与研究文件，未修改产品、依赖锁或 PRD，未调用模型。结论依据当前实际 API、有限反例和既有 78 项能力合同，代码规模是设计预算，尚未形成重构后的测量值。

## 一、执行

运行层首选 Python 标准库 `asyncio.TaskGroup`，插件主机继续使用 `graphlib.TopologicalSorter` 与 `AsyncExitStack`。这样把任务收束和依赖生命周期交给成熟实现，同时保留少量仿真语义。

### 1.1 取消由结构承接，业务失败仍须明确

`taskgroup_boundary_probe.py` 在 Python 3.12.12 中实际创建拥有运行资源的子任务，外部连续两次取消其 TaskGroup 所在任务。第一次取消进入子任务异步清理，第二次发生于清理等待期间；结果为 `cleanup_started → cleanup_finished → resource_close`，调用者最终收到取消异常。这个具体窗口无需当前自定义反复 shield/gather 循环。下一版应将运行及其资源清理放入受 TaskGroup 管理的子任务，使调用者取消被组的退出过程承接。

[Python 3.12 官方合同](https://docs.python.org/3.12/library/asyncio-task.html#task-groups)规定，子任务非 `CancelledError` 异常触发同组取消。单独子任务取消可以正常退出任务组；`probe.py` 已重现“子任务记录写入后自行取消、组正常退出”。因此动作进入 handler 后的失败或取消仍要设置本步骤失效标志，完整发布检查这个业务事实。TaskGroup 不认识数据库写入、已付费请求或步骤完整性。试验也没有证明任意嵌套异常组合；同时异常、SIGINT、close 异常、取消发生在发布前后仍是迁移验收项。

AnyIO 暂不成为 Core 的运行契约。其 [CancelScope 与 shield](https://anyio.readthedocs.io/en/stable/cancellation.html)在自有取消模型内可用，本次重复取消实验通过；外部直接 `asyncio.Task.cancel()` 在清理等待中却穿透 shield，清理未结束。为兼容 Python 嵌入调用者仍须另加边界，当前总胶水并未更少。若将来确需 Trio 等多后端，AnyIO 才作为备选；Starlette 自身使用 AnyIO 不要求仿真调度整体迁移。

### 1.2 队列与主机的最小保留量

主体同一时点的信号合并、同主体互斥、首次提交顺序、再次激活轮次、serial 与 independent 的业务区别、collect 对 incomplete 的处理，属于必须保留的调度合同。队列、固定数量 worker 与任务收束使用标准库，Actor mailbox 的合并规则保持一个小实现。有限 AnyIO stream 实验处理 1000 项且容量有界，但该次调度只观测到一个同时活跃消费者，不能充当三 worker 真实并发证据。

Host 保留 DAG 校验、显式服务提供与依赖、schema/initialize 装配、step 前后 hooks、先 quiesce 后逆序释放资源；不引入 pluggy 的通用 hook 调度层。插件依赖顺序不会自动证明业务行动可交换。step/phase 使用明确 Python 函数；当前没有离散事件优先队列的真实消费者，SimPy 不进入首版。权限沿 scope 与角色 SQL 条件，由统一 Access 在发现、描述、执行和读取处消费；通用授权平台另立需求后再选。

## 二、文件

VFS 首选 Bashkit Python/Rust 组合，保留小型原生回调桥。关键限制进入设计合同：普通 shell 对单文件读取可能整文件物化，大正文通过完整可枚举的分片目录提供。

### 2.1 两个现成 shell 的真实边界

Bashkit 0.18.2 的 lazy 文件实验将 8 MiB 文本交给 `head -c 4`，首次回调仍产生全部 8 MiB，第二次读取使用缓存。[官方 Python API](https://bashkit.sh/api/python/)的 lazy callable 解决延迟装载，不等于范围读取。该版本 `tail -c` 不支持，`tail -n` 可用，具体命令兼容性必须作为接口说明。

just-bash 3.6.0 的相同 lazy 文件也在首次 `head -c 4` 时完整物化 8 MiB；`tail -c 4` 可用，两次命令只触发一次 lazy 回调。[官方 IFileSystem](https://github.com/vercel-labs/just-bash/blob/main/packages/just-bash/src/fs/interface.ts)以整文件读取为主要接口。切换它不会自动解决大文件的小读放大，还将给 Python 内核增加 Node 生命周期及 IPC。当前首选 Bashkit；需要 TypeScript 宿主或实际必需的命令覆盖时才考虑 just-bash。两者这次均未测 RSS、持久化大世界或 IPC 吞吐。

### 2.2 可落地的分片合同

文件投影声明最大单片字节数，提供 manifest，记录原文总字节、稳定版本、片段顺序及取得所有片段的方法；小正文仍可呈现普通文件，大正文呈现目录。每个片段本身就是有界文件，因此 shell 的整读成本被限制在单片。原文没有自动摘要、截断或隐藏末尾；读完整目录的成本仍随完整信息量增长。

`filesystem_probe.py` 使用现有原生桥，将 8 MiB ASCII 原文呈现为 128 个 64 KiB 文件。读首片 `head -c 4` 触发 64 KiB 回调，末片 `tail -n 1` 同样触发 64 KiB，拼接全部片段到临时真实文件后与原文逐字节相等。这个试验的源正文常驻内存，因此证明的是请求放大量与完整性，不是总内存上限。64 KiB 是试验参数，正式配置须明确；UTF-8 文本按有效字符边界分片，二进制须采用保字节的正式读取合同，不能把 Bashkit 文本 stdout 当任意二进制管道。

分片目录、manifest、稳定版本和现有原生桥预计增加或保留约 300–500 物理行，计入总维护量。RealFs 整文件命令同样没有自动变成范围读取；整大文件显式导出可作为用户选择，其磁盘和整读成本必须可见。OpenViking 的 URI 与层次组织可借鉴，它的上下文检索系统不承担 shell 文件一致性；本次不直接引入其完整运行服务。

## 三、接口

HTTP 首选 Starlette + Uvicorn；APSW 继续承担只读查询，服务方法保持薄 Python 接口。普通投影页、不可变序号 tail 与完整点视图保留各自身份合同。

### 3.1 替换传输基础设施

现有手写 HTTP 连接、接纳槽、线程、socket timeout 和 503 处理应由 Uvicorn 的成熟传输层替换。Starlette 提供路由、lifespan 和响应；当前没有必须由 FastAPI 生成 OpenAPI 的消费者，不为将来可能性增加一层。官方 [Uvicorn 流控与资源限制](https://uvicorn.dev/server-behavior/)是选型依据，具体慢请求体超时、代理部署及关闭期限须使用所选版本实际配置验证。

`asgi_fts_probe.py` 在隔离环境 Starlette 1.7.0、Uvicorn 0.52.1 上验证 lifespan 进入/退出及同步路由中 SQLite 创建、查询、关闭处于同一线程，返回 HTTP 200。使用的是 TestClient，未运行真实 Uvicorn socket；安装时补齐 httpx2 测试依赖，环境准备错误与最终结果分开解释。迁移仍须通过真实慢 body、慢 response、并发饱和、断开、关闭与后台 prepare 并行状态读取用例。Starlette worker 线程与 APSW 连接所有权要一致，不能把 event-loop 创建的裸连接交给任意 worker。

JSON/base64 最终 wire budget、巨正文引用、精确 total、游标绑定、权限及相关数据版本、完整点身份、历史准备生命周期均由应用保留。ASGI 替换传输，不替代这些数据语义。准备历史视图继续后台隔离；status 读取不能等待整个恢复操作。

### 3.2 搜索与授权

大量行动模板采用 SQLite FTS5 的现成 MATCH/BM25，普通规模保持简明索引。实验用 SQLite 3.50.4 创建 FTS5 虚表，参数化 MATCH 查询并按 `bm25, rowid` 排序，正确找到 publish 模板；排名函数来自 [SQLite 官方扩展](https://sqlite.org/fts5.html)。这只证明 API 可用，未证明中文分词或任意子串召回适合现合同。Unicode61 与 trigram 等 tokenizer 的选择须以真实模板语料验证；空查询仍明确列全，权限筛选与精确总数保留，模板不按 actor×object 预展开。

保持 `find/describe/invoke` 三个 meta 入口。PydanticAI 的 Agent ToolSearch 组合没有提供当前宿主所需的独立工具检索层，不为一项能力迁入整个 Agent 框架。查询继续 APSW 参数化与白名单小表达式，避免第二套连接与事务所有者。短阶段数字与物理调用归属先满足现观察消费者，OTel 整 SDK 不列为首版依赖。

## 四、规模

采用相同物理行口径统计人工维护源码，包含空行与注释；JSON 中同时提供非空行作补充，但本节全部采用物理行。排除依赖、生成目录、研究工件和数据，源码文件明细可重算。

### 4.1 当前与目标

当前 kernel 为 6808 行，resource_managers 为 2400 行，activation_pool 为 564 行，三者共 9772 行。整个 `src` 为 12450 行，内含 1016 行内置插件；原生桥另有 286 行，因此 Python/Rust 库共 12736 行。库 tests 为 15222 行、150 文件。示例 662 行；skill Python 为 189 行，计入 HTML 资产后为 316 行。保留 UI 的 src 为 2507 行、tests 197 行、构建入口 49 行。这些前端与示例数量独列，不能被“Core 很小”的说法隐藏。

独立估算建议：语义 Core 1200–1800 行、适配器与公开辅助 5700–7200 行、内置机制 1000–1500 行，全部 Python/Rust 合计 7900–10500 行，可取约 8k–10.5k 为设计预算。原生桥包含在适配器内，提供方、存储、记忆、Thread、HTTP、结果、工作区及运行入口都包含。下界较进取：完整留证、恢复链、范围引用和并发语义很难靠选库全部删除；若它们仍采用现有实现，8k 很可能达不到。当前报告不承诺最优行数，也不通过更短写法或搬到插件目录达成数字。

可替换的是手写传输、重复取消收束、提供方通用连接/限流基础以及没有必要保留的泛化包装。必留的是主体身份、同 moment 上下文与游标、动作真实副作用和预算、步骤发布、Thread 原文、记忆三个独立开关、引用寿命、数据可见性及领域机制。换库的可删除行数要在真实消费者完成后以 diff 计量；这里没有将 resource_managers 的 2400 行全额视为可删。

### 4.2 验收范围

现有能力表去重后为 78 项。A/B/E/S 共 23 项覆盖主体、机制与调度，迁移重点是实际 mailbox/顺序/hooks/取消故障窗口；L/M/R 共 27 项覆盖完整模型输入、物理重试、记忆与资源归属，任何新 SDK 或队列都要经过现有真实消费者的确定性替身；N/O/P/Q 共 23 项覆盖文件、观察、持久化和数据页，追加分片全读、原文逐字节、传输慢客户端和完整点恢复；U 共 5 项覆盖运行入口、示例及工作台，使用真实产生的产物验收。上述分组共 78 项，能力不随代码目标缩减。

保留现有测试中的业务与失败断言，替换绑定旧私有实现的 fixture。优先复用当前约 15.2k 行库测试及独立 UI 测试；测试规模无需与产品行数同比下降。本次有限实验尚未替代该迁移套件，下一阶段应逐组件验证后再评估代码量与运行成本。

综合建议是以 TaskGroup、标准库 Host、Bashkit 有界文件投影、Starlette/Uvicorn 和 APSW 原生查询组成首版。成熟组件承接通用机制，仿真必须解释的失败、时序、信息完整性与恢复身份保持显式。所有实验脚本和小结果位于 `runtime-selection/`，临时 npm/venv 位于 `/tmp/society0-runtime-selection-20261004`；本轮没有后台任务或模型调用。
