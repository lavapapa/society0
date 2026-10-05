# OpenViking 与主体信息视图研究

本文保留选型阶段的试验条件、测量和当时结论。文中的“当前”“候选”“尚未实现”均指该次试验时点；现行接口与装配方式见[信息交互合同](interaction-contract.md)与[工作区合同](workspace-contract.md)。阶段状态与后续验收由 [TODO](TODO.md) 记录。

本报告于 2026-10-04 核对 OpenViking 官方文档与源码，讨论 Society0 单一共享环境中的主体信息访问。固定研究版本为官方仓库 [9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14](https://github.com/volcengine/OpenViking/commit/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14)，提交时间为 2026-10-03T11:10:51Z。以下源码链接均固定此版本；线上文档会继续变化。本轮下载源码至临时目录只读研究，没有安装服务、访问凭据或执行性能测试。建议采用其 URI、目录导航、分层信息与内容索引分离机制；核心的权威状态、行动资格、完整 Thread 和固定观察版本继续由 Society0 的运行合同定义。

## 一、定位

OpenViking 为信息、记忆和技能提供统一的文件式访问表面。该表面可以服务单一环境中的不同主体，无需为每位主体建立一份环境或独立服务。

### 1.1 版本与实现

固定版本 README 将官方文档指向 [docs.openviking.ai](https://docs.openviking.ai/en/concepts/05-storage)。本轮也直接读取了 [docs.openviking.net 的同一页面](https://docs.openviking.net/en/concepts/05-storage)，两者均明确写出 AGFS 已重写为 Rust 的 RAGFS，存储分层与原文描述相符。因此域名本身不足以判断文档属于旧 Go 或新 Rust 实现。

当前 Python [get_binding_client](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/openviking/pyagfs/__init__.py) 加载 Rust PyO3 原生绑定，AGFSBindingClient 保留为命名别名。仓内 crates/ragfs/ORIGIN.md 仍提到自动选择及 Go 回退，当前入口未实现这一分支，故具体运行判断以源码为准。配置与部分接口继续使用 agfs 名称，也不能据此推断仍运行旧服务。

### 1.2 URI 与主体范围

[Viking URI 文档](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/04-viking-uri.md)定义 resources、user、agent 等空间；请求中的 account 身份加入物理路径，user 决定私有内容范围，`viking://~` 按当前请求展开为用户根。agent 空间在该版本主要承载账户共享技能与配置，不能按名称理解为每个仿真 Agent 的独立 World。

这种“公共内容共享、私有内容按身份定位”的方式适合借鉴。Society0 的一个运行仍只有一个环境；主体视图携带主体身份、角色和允许读取的版本，指向共同内容。主体自己的笔记与主观记忆具有独立所有权，公共账表、规则和公告无需复制进每个人的目录。OpenViking 的 account/user/peer 是上下文产品身份模型，映射到运行、主体和关系需要显式决定，不能原样套入组织、员工和共同知识的社会语义。

## 二、访问

目录组织、内容读取和检索解决不同问题。界面上的“按需加载”只有落实到实际读取路径，才能约束 CPU、内存和磁盘工作量。

### 2.1 L0、L1 与完整内容

[Context Layers](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/03-context-layers.md)中的 L0 为目录摘要，L1 为目录概览，L2 为原始文件或解析后的正文。L0/L1 通常存为目录 sidecar，可能缺失或单独存在；普通文件并不都具有一组独立 sidecar。PDF 等输入可能转成 Markdown，因此 L2 的完整正文与源文件字节保真也须分开验收。

可借鉴的是先发现目录、读取说明，再选择明细。Society0 可以用领域作者给出的集合说明、字段解释及确定性统计提供导航，按需增加模型生成概览。摘要应附来源与有效时点，主体仍可完整读取相关原文。完整 Thread 继续按既有会话语义传递，不能因引入 L0/L1 就改成摘要替代历史。

当前摘要具有覆盖与新鲜度记录，包括直接子项总数、被采样项及尚未反映的变化。大量子项会采样，父目录刷新可能等待变化比例达到阈值，阈值未达到时也没有定时更新承诺。由此推论，摘要适合导航与语义检索，当前库存、义务和可行动条件应从及时维护的权威视图读取。不能将“摘要已生成”理解为“已包含全部最新事实”。

### 2.2 读取路径的真实代价

公开内容读取经 [FsService.read](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/openviking/service/fs_service.py)，先调用 VikingFS.read_file 取得全文，再根据 offset/limit 切分行。[VikingFS 的 read_file 与 read](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/openviking/storage/viking_fs/_ops.py)也显示：read_file 整份读取并解码；带行窗口时使用 splitlines。行分页可以缩短返回结果，成本仍与完整文件大小相关，超长单行尤其无法靠行数预算约束。

底层 read(offset,size) 确实把字节范围交给 RAGFS，但后端行为不同。固定版本 [localfs::read](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/crates/ragfs/src/plugins/localfs/mod.rs)先 `fs::read` 整份文件，再对范围 `to_vec`；[s3fs::read](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/crates/ragfs/src/plugins/s3fs/mod.rs)区分全读和 get_object_range。接口具有 offset 不足以证明本地读取有界，也不等于迭代流式返回。全读还可能经过 Rust 缓冲、Python bytes、文本解码、行列表与响应编码，实际峰值必须测量，不能预设零复制。

对 Society0，主体读取一条记录、一个页面或一段大正文时，底层访问量应由所选内容与明确预算决定。把整个 World 映射成一个巨大 JSON 文件，再交给 shell 的 head、grep 或读取行窗口，无法满足这一条件。虚拟目录可对应查询集合，正文可对应固定记录或不可变内容引用；文件式表面无需预先导出所有内容。

### 2.3 检索与权限

当前类仍名 [HierarchicalRetriever](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/openviking/retrieve/hierarchical_retriever.py)，但源码和[检索文档](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/07-retrieval.md)说明现行路径为每个查询一次全局向量检索，目录、权限、类型和层级作为过滤条件，再按配置重排；旧目录优先队列与父子分数传播已移除。find 使用单查询，search 可根据会话信息调用模型规划多个查询。search 的 limit 按规划查询应用，汇总条数可能超过该值。

Society0 可借鉴 URI 与目录范围传入检索、结果返回可继续读取的正文引用。精确查账与语义搜索应保持不同合同：向量 top-k 无法保证枚举全部业务相关记录，不能用检索结果数量充当目录总数。模型规划、嵌入和重排均有独立服务成本，应作为显式可选能力计量。

[ACL 文档](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/15-acl.md)与 [_AccessMixin](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/openviking/storage/viking_fs/_access.py)将文件与检索置于共同身份范围，适合参考。该版本 ACL 默认关闭；开启后共享根默认允许账户用户 manage，需明确建立限制边界。ACL 数据跟随索引更新，官方明确不保证强一致。Society0 的信息资格与行动资格可复用同一主体上下文和领域判断，但即时可见性撤回、持仓条件与交易资格仍需在权威环境中判定，不能依赖尚在更新的向量记录。

## 三、运行

把信息访问引入实时仿真，必须同时说明内容、派生索引和固定版本的关系。后台任务完成、文件可读与完整步骤可恢复是三个不同事实。

### 3.1 内容与索引

[存储架构](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/05-storage.md)将内容保存在文件层，向量层保存 URI、向量、元数据及检索文本。该分离不意味着没有正文重复：部分记忆正文也写入索引字段。磁盘成本要包含原文、sidecar、索引文本、向量、队列、缓存及快照，不能仅统计源目录。

[路径锁与恢复合同](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/09-transaction.md)明确文件、向量库与队列之间没有统一原子事务。删除优先移除索引，部分失败可能暂时漏检；资源导入和 session 处理通过持久化任务完成后续工作。对仿真主体而言，漏检若影响决策就是语义变化，所以必要记忆或信息读取要等待所需索引水位，或从权威路径精确读取，并明确当前搜索覆盖范围。

[QueueFS](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/16-queue-lifecycle.md)用同一状态快照中的 pending 与 processing 判断是否排空；排空不等于所有任务成功，也不能阻止新任务进入。接入时应读取对应任务的结果与错误，不能用全局队列空作为某个仿真步骤完成证明。session 记忆提取恢复可能重复模型工作，生成结果也可能变化，现有 Thread 的调用事实与失败留证不能因此交由其自动记忆流程替代。

### 3.2 并发与缓存

[RAGFS Cache](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/guides/14-ragfs-cache.md)是可选读缓存，文档要求同一命名空间单进程写者，且后端修改经过 RAGFS。跨进程路径锁不取消这一缓存前提。[缓存实现](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/crates/ragfs/src/cache/wrapper.rs)对部分范围读取绕过完整文件缓存，完整读取可合并同键并发未命中。因而读并发、写协调及缓存一致性必须分别考察。

语义处理使用并发限制与分批模型调用，CPU 与内存仍受解析、正文物化、批次大小和目录规模影响。共享服务可以避免每位主体常驻一份索引；私有主体内容、各请求响应和缓存仍可能重复。实时账表若每步都触发摘要、嵌入及父目录传播，会增加模型调用与索引维护，优先将这类信息作为直接查询视图，静态知识与确实需要语义检索的记忆再进入派生处理。

### 3.3 持久化与固定观察

[Snapshot Guide](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/guides/15-snapshot.md)描述 gitoxide 文件树版本；普通 write 不自动提交版本，ACL 和向量索引不随历史版本保存。快照不是任意并发 I/O 的全局原子视图，指定范围仍需先结束相关写入。restore 修改当前工作区，派生索引可能随后异步重建，故文件恢复成功与可按旧版语义检索之间仍存在距离。

这无法直接提供 Society0 的“同一 observed revision 跨页读取全部所需内容、资格与时点”。第一版应由环境在既定安全点提供固定 view；URI 定位内容，view 定位该次可读取的版本，两者同时进入继续读取位置。当前索引达不到指定水位时返回明确未就绪，历史内容已回收时返回过期，避免读取过程中混入新版。若未来使用 OpenViking 快照，需要额外验证文件、权限与索引之间的版本关系；本轮不引入其 Git 内容寻址或新增任何哈希机制。

多写存储也不提供通用回滚：[官方合同](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/docs/en/concepts/14-multi-write-storage.md)允许主存成功而备份确认不足时报错，主存变化仍保留。它适合按需求评估备份与读路由，不能取代跨混合介质的完整运行恢复边界。

## 四、采用

先确定真实消费者，再决定依赖范围，可以把 OpenViking 的有效机制引入主体信息访问，同时保持普通环境实现简短。

### 4.1 依赖选择

若已有 OpenViking 服务，直接复用官方 [openviking-sdk](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/sdk/python/pyproject.toml)作为可选知识与记忆检索客户端最现实，该包声明依赖仅 httpx。当前主体提供方可将检索结果转换为环境信息引用，完整正文仍能读取。服务故障不能使基础规则驱动、精确账表访问或完整 Thread 失去可用性；是否等待该服务取决于本次决策是否必须依赖其信息。

把完整 OpenViking 嵌入 Core 会引入原生 RAGFS、向量实现、解析栈、服务与模型处理依赖，见[项目依赖](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/pyproject.toml)。它的默认会话处理、索引时效、整文件读取及快照范围还需逐项适配，当前证据不足以支持作为全环境强制底座。RAGFS 独立绑定虽可技术复用，已发现本地范围读取整读行为，且 Society0 无需额外承担其多后端与版本管理体系，因此首版优先借鉴 URI/view 与信息层次，使用项目已选定的具体混合存储。

固定版本主项目声明 AGPL-3.0，各子目录另有许可声明。正式复制源码或分发集成依赖前需按所用组件确认许可范围；本报告没有作法律结论，也未复制其产品实现。

### 4.2 最小合同

建议先让信息访问共享一个调用上下文：主体身份、读取 view 与运行逻辑时点由引擎传入，LLM 和 rule 驱动获得相同资格。最小信息面为 `list(uri, cursor, limit, max_bytes)`、`read(uri, cursor, max_bytes)`，可选 `find(query, scope, limit)`。这些是建议语义，名称由 Core 规格统一。返回内容须携带规范 URI、view 和继续读取位置；目录提供精确总数，语义检索则明确返回的是候选数及索引覆盖范围。

L0/L1 是可选表示，read 默认保留取得完整正文的路径。每条信息引用都按主体资格检查，list/search/read 共用判断入口。行动描述可在同一目录体系被发现，但执行仍通过环境 action，重新判断当前资格与业务条件。主体私人工作区允许普通笔记写入；修改账表文件不能绕过领域行动直接改变公共世界。Bash 可作为这些操作的表达前端，首版不要求复刻 POSIX，也不引入任意存储插件注册框架。

## 五、试验

建议用四项小试验决定是否进入依赖集成。它们尚未执行，以下阈值表达需要验证的复杂度，而非已有性能成绩。

**读取粒度试验：** 同一份逐字节一致的正文分别为小文件、大文件和超长单行，固定请求 64 KiB；比较官方文本 read、本地底层 range 与现有项目分段读取，记录实际磁盘字节、CPU、峰值 RSS、返回内容和继续位置。成功标准是候选路径不会因未请求的正文增长而同比增加物化内存；一切截取均可继续完整读取。

**共享与主体视图试验：** 固定一份公共集合，让 1、10、100 个主体分别浏览共同信息和私有笔记，测初始化 RSS、每主体增量、目录扫描量、缓存占用和实际复制字节。加入角色或关系变化，验证目录、检索与直接读取在同一 view 下资格一致。目标是公共正文共享，主体开销主要由真正私有内容与当前请求决定。

**实时与失败试验：** 在固定观察版本分页期间产生新写入、延迟语义任务、切换资格并模拟进程退出，验证旧页内容保持固定或明确过期，新查询报告索引未就绪，失败任务不被队列排空掩盖。恢复核对完整 Thread、记忆正文与已有调用回执，确认没有自动裁剪或重复模型提取。

**更新代价试验：** 固定活动记录和每步变化量，逐档增加冷历史，连续运行短段更新；分别计量权威写入、摘要生成、嵌入、重排、目录刷新、文件/索引/缓存/快照磁盘增长。使用预制模型返回即可先验证调用数量与工作量，真实效果比较另行安排。官方 [vector benchmark](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/benchmark/vectordb_perf/README.md)明确排除了 Server、AGFS、embedding 和 rerank，其结果不能替代此端到端负载。

当前建议是把 OpenViking 作为信息组织与可选语义检索的参考，单一环境继续拥有主体资格、业务状态和时间。URI、目录与可选概览能降低主体查找信息的负担；它们的实现仍需满足完整内容可达、固定版本不混读、共享数据不按主体复制，以及代价由实际访问和变化决定。
