# 执行、插件、文件视图与观察接口的成熟组件评估

本报告于 2026-10-04 只读核对 Society0 工作树提交 `0ae6f7dd3a5baac4fbaacc272c500c0cbcb0e4d0` 的实现与官方资料。范围为 Runtime、ActivationPool、PluginHost、ShellSession、Workspace、Observation 和 Memory；没有修改产品、安装依赖、运行试验或调用模型。结论是优先验证成熟 ASGI 服务器替换自有 HTTP 接纳与线程管理、结构化并发替换分散的任务收束。插件排序和 shell 已采用成熟基础组件，进一步替换应以删除实际维护代码为准。具体性能收益尚待同负载本地试验，本文不提供推测的加速数字。

## 一、问题

当前体系把多个经典问题集中在一个进程：网络等待的任务生命周期、按主体串行的邮箱、插件依赖图、仿真阶段顺序、文件式访问、实时读接口和向量候选检索。先分清这些问题，才能判断一个库真正覆盖了多少代码。

### 1.1 执行与装配

`src/society0/activation_pool.py` 的 `_submit_signal` 保存按 key 的 pending 信号与去重集合，`_scheduler` 从 asyncio.Queue 取 key 后逐项 create_task，`_execute_key` 再等待 serial lock 和 capacity semaphore。这里活动执行容量受到限制，但已创建的等待 task 数由排队 key 数决定。合并信号、同主体互斥、首次提交顺序、round、全步激活额度和 drain 对瞬时空队列的处理属于邮箱语义；task 集合维护、取消传播、异常回收属于通用并发基础设施。当前实现两者交织，适合先拆清责任再用 TaskGroup 或 AnyIO 接管后一部分。此处是源码复杂度判断，尚未新增压力测量。

`kernel/runtime.py:Runtime.close` 另建 close_task，再循环 shield 防二次取消打断收尾；`kernel/plugins.py` 用两个 AsyncExitStack 实现先 quiesce 再逆序释放资源。Memory 与 shell 又分别跟踪操作或回调任务。重复的任务归属、取消排空和 closed 检查值得收敛到少数结构化作用域。领域 handler 部分执行后抛错须使整个步骤失败、完整点只能在所有必要钩子完成后发布，这些约束仍由 Runtime 负责。

`PluginHost._order` 已经直接使用 graphlib.TopologicalSorter，资源清理直接使用 AsyncExitStack。因此“插件系统都是自造”的判断不成立。自有部分是显式 provide/require、声明服务依赖、业务 before/after hooks、quiesce 和 schema 初始化合同。依赖图描述安装顺序；Phase 描述业务因果顺序，两者应继续分开。

### 1.2 文件、读取与记忆

`kernel/shell.py` 使用已发行的 Bashkit 0.18.2，解析器、命令和管道均来自上游；自有 Rust capsule 把原生 OverlayFs 接到 Python Workspace/Information。`workspace.py` 按 actor+path 保存索引，原文为不可变工件，保存 upper 的实际变更。共享信息以调用者视图读取，未给每位主体复制完整环境。`WorkspaceSession.callback` 的 read 分支明确调用 `read_artifact(...,size=max(1,row[1]))`，会返回完整文件 bytes；这是真实整文件物化点。lazy 首次加载与字节范围读是不同合同，Overlay 避免整个工作区快照重复不等于所有命令都流式。

`kernel/observation.py:make_server` 自行维护 BaseHTTPRequestHandler、Content-Length 请求读取、JSON 错误、最终响应编码、BoundedSemaphore、每请求线程、socket 超时和关闭。`ObservationService.call` 每请求创建 Observation，异步方法用 asyncio.run；HTTP 没有跨请求 StageReader 连接复用。相比之下，Python 长作用域 Observation 已有复用。keyset 页、追加 Thread tail、完整点水位、巨消息原文引用及固定 complete view 是查询语义，与 HTTP 传输实现可独立替换。

`kernel/memory.py` 保存权威正文、原始向量、作业和历史；Chroma 作为派生检索层。当前 recall 先完成待处理作业、同步索引，再按 actor/visible_step 过滤取 `top_k*2` 候选，之后进行时效与重要性重排和正文去重。历史召回显式建立临时集合。换向量库会影响候选集合、距离、并列顺序和最终认知，必须比较这些有效结果；保留同一批 embedding 向量能够让比较完全脱离付费服务。

## 二、执行

执行层最值得复用的是资源与任务生命周期；仿真时间推进和业务完成条件需要由明确的研究语义驱动。

### 2.1 TaskGroup 与 AnyIO

[Python TaskGroup](https://docs.python.org/3/library/asyncio-task.html#task-groups) 从 Python 3.11 提供作用域内子任务等待、失败取消和异常组。项目最低 Python 3.12，可以先用标准库消除手工 task set、done callback、gather 收尾。它并不提供按 actor 信号合并、activation round 或业务额度；默认一个子任务异常取消同组也须对应 fail_step，显式 collect 的正常 incomplete 返回值仍可继续。

这里的失败传播有具体例外：子任务抛出 `CancelledError` 不按普通异常触发同组失败。Society0 已进入行动处理器后的取消须使步骤失效，应继续由明确业务边界登记；验证写入后子任务取消仍禁止完整发布。不能用 TaskGroup 本身代替这条完整点规则。[Python 3.12 合同](https://docs.python.org/3.12/library/asyncio-task.html#task-groups)。

[AnyIO 取消作用域](https://anyio.readthedocs.io/en/stable/cancellation.html)进一步提供分层取消、shield 和 timeout；适合目前多处二次取消清理逻辑。官方明确 level cancellation 与 asyncio 的 edge cancellation 有差异，混用某些 asyncio 同步原语可能产生反复取消甚至忙等。推荐原型以整个 Phase 的任务树为边界，不逐个函数零散包 CancelScope；SQLite 同步写者继续留在其所有者线程。线程任务无法靠协程取消强制终止，纯 CPU 压缩的排空也仍需处理。

最小验证使用固定规则任务，比较 TaskGroup 与 AnyIO 两个短候选在同 actor 多信号、运行中补位、首次失败阻止排队任务启动、两次外部取消、清理自身异常和安装失败上的行为。度量 pending key 数、真正创建的 task 数、峰值内存以及停止到资源释放时间。成功门槛为原合同全等且手工生命周期代码明显减少；TaskGroup 足够时优先标准库。若改成有界 worker 读取 mailbox，必须保留同主体串行和无新增常驻 actor task 的性质。

### 2.2 pluggy、importlib 与 SimPy

[pluggy](https://pluggy.readthedocs.io/en/stable/)是 pytest 的 hook 引擎，适合第三方插件发现、签名约束、hook 顺序和结果收集。其成熟度有实际生态支撑；它没有直接提供本项目的异步资源 DAG、数据库 schema 合并与两阶段关闭。当前两个 step hooks 换成 pluggy 很可能保留原 Host 外再添一层。建议保留 [graphlib](https://docs.python.org/3/library/graphlib.html) 与 ExitStack；若出现外部分发插件需求，首先采用 [importlib.metadata.entry_points](https://docs.python.org/3/library/importlib.metadata.html#entry-points) 发现显式选择的工厂。只有真实多实现 hook 扩展需要包装器、签名校验或插件生态管理时，再评估 pluggy。验证以两个独立 host、依赖缺失/循环在安装前失败、安装中断逆序清理及恢复不重复初始化为准。

[SimPy 的时间与调度](https://simpy.readthedocs.io/en/latest/topical_guides/time_and_scheduling.html)使用离散事件队列，同时间事件按插入顺序串行处理，模拟并发与 CPU 并行有不同含义。它适合运输延迟、机器占用、排队资源、合同到期等机制，能够替代未来自写的离散事件日历。当前 CodeSchedule 是显式阶段式共享状态执行，直接换成 SimPy 会重定业务时间语义。建议将 SimPy 放在确有上述过程的机制插件内部，以到期事件产生本阶段动作；LLM 网络等待保持墙钟等待，不让其自动推进 env.now。最小验证固定两辆运输车、同时间交付和资源争用，对照事件顺序、时间和恢复后剩余事件；SimPy 运行中 generator 的持久化需另定状态表达，不能把进程对象当完整检查点。

## 三、基础设施

文件和观察接口具备清楚的替换缝隙。选择时同时考虑语言边界、是否新增服务，以及原始内容和固定版本能否完整保留。

### 3.1 VFS 的候选边界

[Bashkit Python API](https://bashkit.sh/api/python/)已提供原生 shell、custom builtins、文件注入和快照；本项目适合继续直接复用其 Rust 引擎。优先将目前额外维护的 Overlay/callback 桥整理成上游可接受的窄扩展，核查现行官方 API 是否已经覆盖，再决定删除自有桥。验收需要真实 cat/head/tail/jq、打开文件后修改、符号链接、UTF-8 边界、结果大正文、idle 保存与关闭取消；原文范围和 upper/lower 语义不能由“支持 snapshot”一句代替。本轮没有重新安装或试运行。

[Vercel just-bash](https://github.com/vercel-labs/just-bash)提供 TS 的模拟 bash 与虚拟文件系统，[bash-tool](https://github.com/vercel-labs/bash-tool)将其接入工具调用。它是应保留的直接替代候选，不能因 Bashkit 已接入便跳过。替换价值须来自更少自有桥代码或更完整的文件/命令行为；Python 核心通常需 Node 进程或嵌入 JS 的边界，私有工作区恢复与授权回调仍要连接。最小对照用同一批脚本和大文件，记录激活创建/释放、原文是否整读、IPC 输入输出字节、每活动实例 RSS 与变量/cwd/文件恢复；先用共享 Node worker 的有界会话原型，避免默认每 actor 一进程。当前只核官方入口与职责，未把其性能或持久一致性视为已验证。

[fsspec](https://filesystem-spec.readthedocs.io/en/latest/features.html)适合对象存储、远端文件、缓存与可 seek 文件句柄，能替换将来自己编写的外部文件后端。它不执行 bash，也不定义 actor 权限、仿真 revision 或完整步骤。block cache 应有实际容量与版本身份，缓存旧正文会改变主体输入；多文件 transaction 的具体保证依 backend，不能当作 World+Thread+Memory 的统一提交。PyFilesystem2 的 [FS/stream 抽象](https://pyfilesystem2.readthedocs.io/en/latest/guide.html)同样适合普通文件操作，但最新 PyPI fs 发行仍为 2022 年的 2.4.16，维护节奏低于 fsspec；在 Python 3.12+ 上引入需先验证依赖与实际接口。两者都无法直接取代当前 SQL 视图授权层；没有外部后端消费者时不增加新适配层。

[OpenViking 官方仓库](https://github.com/volcengine/OpenViking)把 URI、文件目录、L0/L1/L2 与上下文检索结合起来。现行网页展示的能力适合知识资料库；此前固定源码 `9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14` 的[存储说明](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/05-storage.md)和本仓 `docs/core-next/openviking-study.md` 可复核 Python/Rust RAGFS 路径。本轮重新核对官方入口，但未重新深审最新全部源码；旧固定版本结论不冒充当前每个实现细节。直接引入适合独立知识库服务；作为整个运行内核会引入文档处理、摘要、检索索引等额外生命周期。L0/L1 应是可选发现材料，L2 与完整 Thread 仍须可读；生成摘要或索引也不能替代已提交仿真版本。许可和发布方式需按实际选定版本核对，官方当前仓库标示 AGPL-3.0。

语言选择因此保持 Python 调度和成熟 Python 生态，Rust 承担已有 Bashkit/SQLite/vector 热循环；TS 适合 UI 或已有客户端。将运行核心迁到 TS 会增加现有 SQLite/记忆/原生文件桥适配；单独 Node shell 进程又增加 IPC 和进程驻留，当前没有证据表明收益足以覆盖这些成本。

### 3.2 HTTP、分页与通知

推荐优先验证 Starlette+Uvicorn 取代自有 HTTPServer 层。[Uvicorn](https://uvicorn.dev/server-behavior/)已有读写流量控制、连接管理、503 并发接纳和优雅退出；[配置](https://www.uvicorn.org/settings/)提供 concurrency 和 shutdown timeout。[Starlette lifespan](https://starlette.dev/lifespan/)负责服务资源进入/退出。若需正式 OpenAPI、请求模型与交互文档，再考虑 FastAPI；当前一个 method RPC 的解析无需先引入庞大模型层。[FastAPI 的 async 合同](https://fastapi.tiangolo.com/async/)同样要求识别阻塞工作。

替换仍需保留最终 JSON/base64 字节预算、业务错误码、正文读取超时与完整视图准备生命周期。Uvicorn keep-alive timeout 是请求之间的空闲超时，不自动等价当前慢 body/read/write 合同；应用接收总时限须另设。其连接数限制含 idle keep-alive，不能把 capacity 数字机械对应现有工作槽数。同步 SQLite 读取适合一次 worker 调用内创建、使用、关闭 reader；Starlette 的默认线程 limiter 为 [40 tokens](https://starlette.dev/threadpool/)，属于共享容量且不会保证同一请求的多次调度落同线程。先保单次所有权，复用收益另测，不创建跨线程裸连接。

[SQLite row-value keyset](https://sqlite.org/rowvalue.html)已经覆盖按稳定键继续分页的基础；保留现有 ordinal/seq 索引和 typed 身份。固定页的 snapshot/revision、持续 append tail、合并 status 快照是三种消费合同。成熟 HTTP 库不替我们决定可见水位、历史视图失效或精确 total；SSE/WebSocket 只改变传输，断开后仍须从持久 seq 继续。恢复后身份、严格页在持续写入下过期与 prepare 单槽均属于领域读协议。

[MCP 官方分页说明](https://py.sdk.modelcontextprotocol.io/advanced/pagination/)适合向外部 agent 提供资源发现与 opaque cursor，可以作为 Observation 外侧的薄适配器。服务端仍要定义 cursor、范围和原文读取；SDK 不自动建立快照、精确 total、字节预算或模型分页意愿。若无外部 MCP 客户端需求，先完成 ASGI 传输替换可获得更直接的删除收益。

最小 HTTP 验证保持当前独立服务进程，用合法慢 body、暂停读大响应、容量饱和、断开、prepare 退出和大 schema 小 status 覆盖同一合同；记录实际线程数、连接数、RSS、响应 p95 和清理延迟。所有输入可本地合成，完全不需要模型。通过后删除旧 socket/server 实现，避免双传输长期并存。

## 四、取舍

向量检索可直接购买成熟实现，但运行可恢复性和主体记忆语义仍需清楚定义。选择顺序应围绕当前维护负担和资源瓶颈，而非库名覆盖率。

### 4.1 记忆的候选

[Chroma 架构](https://docs.trychroma.com/reference/architecture/overview)区分嵌入式、单节点与分布式部署，当前代码已经复用其候选索引。继续使用的优势是既有接口与测试；需要承担 SQL 权威数据与派生索引同步、恢复重建和两套存储的成本。这里不能把 SQL/Chroma 双写称为跨库原子提交，仍需以 SQL 完整点和可重建索引为恢复合同。

[Qdrant 存储](https://qdrant.tech/documentation/manage-data/storage/)提供向量/载荷的内存和磁盘配置，服务端方案适合多进程共享检索与较大索引；成本包含常驻服务、RPC、索引维护和备份协同。官方现有 [Qdrant Edge](https://qdrant.tech/documentation/edge/)提供 Rust 与 Python 的进程内检索，但明确为 beta，不能把 server 的成熟度自动赋给 Edge。qdrant-client local 模式也需与 server/Edge 分开验证，不能用一个名字混报资源特征。

[sqlite-vec](https://alexgarcia.xyz/sqlite-vec/)是可嵌入 SQLite 的 C 扩展，能够减少独立向量服务和索引状态；其 [KNN](https://alexgarcia.xyz/sqlite-vec/features/knn.html)可结合 metadata/partition 约束。值得以现有固定向量做小规模精确检索候选，尤其每 actor 有限记忆时。当前发行 0.1.9，采用前需核对扩展虚表与 APSW Session/恢复的实际兼容性；即使同连接可提交，派生虚表也可能仍应从普通权威向量表重建。精确扫描代价随候选数和维度增长，与 HNSW 的召回/资源权衡不同；不可只测十条数据即替换大库。

最小比较保留完整正文、原始向量、actor/visible_step 过滤、update/delete、时间衰减和正文去重，使用已保存向量或确定性本地向量，无新 embedding 调用。分别测 Chroma、sqlite-vec，只有独立服务需求明确时增加 Qdrant；比较候选 IDs/距离/并列次序、最终排序、恢复前后召回、索引构建/重建、增量成本和峰值内存。检索算法变化导致候选不同，应作为认知语义变化单独批准。

### 4.2 维护证据与行动顺序

本轮从各项目 PyPI 官方 JSON 元数据读取到：AnyIO 4.15.1（2026-09-05）、pluggy 1.6.0（2025-05-15）、SimPy 4.1.2（2026-05-24）、Bashkit 0.18.2（2026-09-22）、fsspec 2026.9.0（2026-09-18）、fs 2.4.16（2022-05-02）、Starlette 1.7.0（2026-09-23）、FastAPI 0.142.2（2026-09-30）、Uvicorn 0.54.0（2026-09-25）、Chroma 1.5.9（2026-05-05）、qdrant-client 1.19.1（2026-09-16）、sqlite-vec 0.1.9（2026-03-31）。元数据入口为 `https://pypi.org/pypi/<distribution>/json`。发行时间用于说明可获得版本与维护信号，不能单独证明适用性或性能；本仓锁定版本也不因本报告自动升级。

建议第一轮原型聚焦 ASGI 传输替换与结构化并发两项，成功即删除相应自有实现；第二轮用已有原始向量比较 sqlite-vec 与 Chroma 的实际代价。PluginHost 保持 graphlib/ExitStack，Bashkit 保持直接依赖并争取缩减自有桥；SimPy、fsspec、pluggy、MCP 和 OpenViking 分别等待离散过程、远端文件、外部插件、外部 agent 连接与知识库的真实消费者。每项验证均可使用本地规则、固定输入和已有原文，研究期间无需 DeepSeek 或任何付费模型。

这条路线把成熟组件能承担的通用责任交还上游，同时保留少量可明确验收的仿真语义。最终采纳依据应是实际删除的维护面、等价行为和同负载资源证据；本报告提供候选边界与验证设计，尚未执行任何替换。
