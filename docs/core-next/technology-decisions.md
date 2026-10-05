# 技术选型

本文固定下一轮 PRD 的采用方案。各项分别说明直接依赖、保留应用语义及排除范围；备选仅在列明条件成立时重新评估，不进入首版自动切换路径。具体版本、隔离命令与结果保存在 [模型研究](../../research/core-next/provider-selection-20261004.md)、[存储研究](../../research/core-next/storage-selection-20261004.md)和[运行研究](../../research/core-next/runtime-selection-20261004.md)。这些选择已有源码或小型试验依据，完整产品、平台部署和账户可用性仍按 PRD 验收。

文件工具与主体插件的后续收敛采用 [专项规格](agent-filesystem-design.md)。本轮新增选型为 ripgrep 的独立 grep-searcher／grep-regex 库处理逻辑原文搜索，路径匹配复用标准库，主体扩展复用 AsyncExitStack；Pi 与 OpenViking 为交互参考，Bashkit 继续承担 shell。研究与隔离反例见 [准备研究](../../research/core-next/filesystem-preparation-20261005/research.md)。这些调整的产品接线由 F01–F08 验收，不以依赖本身支持的 API 推定已实现。

## 一、模型

首版选择 Python 进程内的 Pydantic AI Agent 与模型接口，研究运行、账户授权、完整 Thread 和领域行动事实由 Society0 管理。

### 1.1 请求与认证

**01 模型与工具：直接依赖 Pydantic AI slim 的 Agent、Model/EmbeddingModel。** 使用需要的 provider extras；Agent.iter 推进模型与工具节点，Tool 执行固定元工具。Society0 保留领域行动账本、Thread 水位、物理调用留证和记忆作业。唯一框架备选为 LiteLLM SDK，触发条件是必需提供方或原始字段存在经验证且公开 API 无法解决的缺口；替换后仍只有一个默认协议层。pi-ai 作为协议与 OAuth 行为参考，不增加常驻 Node 模型进程，也不复制其 provider 实现。Agent 的领域适配与实际采用证据见 [运行时复用审查](../../research/core-next/agent-runtime-study-20261004/report.md)；slim 的基础依赖计入部署面。

**02 订阅认证：直接依赖 Authlib，按官方 SIWC 接 ChatGPT 计划。** Authlib 处理标准 OAuth/OIDC 与刷新，服务特有适配负责动态 issued client ID、host 身份、授权 scopes、账户选择和轮换凭据。凭据使用应用独立的用户配置文件，标准文件接口进行原子替换，限制文件读取权限；每账户由一个认证管理器负责刷新。文件位于运行目录之外，不进入仓库或产物。不自行实现 PKCE、签名算法或 JWT 验证。模型调用通过公开 Responses profile 及 extra_body 生成所需工具 namespace。内置 CodexProvider 的旧 backend 路线保留研究记录，正式默认采用 SIWC，不自动读取或复用 Codex CLI 凭据。

官方 DevKit 仅作公开行为参考：当前是未发布的 Node workspace，公开客户端偏文本，且代码许可不适合直接进入本项目。Codex app-server 管理完整 thread/turn 和代理工具循环，SIWC 配置仍要求调用方刷新 token，因此本项目不为认证引入它。其他 coding plan 仅在正式产品边界内使用；GitHub Copilot SDK 属于完整外部 Driver 路线，Claude/Gemini 的 CLI OAuth 不作为通用模型凭据入口。

**03 请求资源：标准 asyncio 许可、SDK 连接池与单一重试策略。** Agent 请求借用本次激活持有的完整 typed 历史；独立提取请求在许可授予后物化完整输入。重试同一水位，SDK 自动重试关闭，所有物理请求可见。RPM 需要时使用 aiolimiter，TPM 按提供方明确预算和返回处理；不引 Router、Proxy、Redis 或跨模型自动 fallback。embedding 动态微批保留逐原文分发的小实现，由成熟接口执行物理 batch 请求。

### 1.2 发现与驱动

**04 动态行动：保留 find/describe/invoke，排名直接用 SQLite FTS5。** 普通规模先按索引筛选，大量模板启用派生 FTS5。模板、目标和主体资格分别处理，文字搜索有空查询列全入口。当前 FTS API 已做最小验证，中文 tokenizer 与真实语料召回列入实施验收。SIWC 不支持原生 Responses tool_search；当前使用固定发现工具，主体资格、目标和完整参数描述由 Actions 的小型绑定门面提供。

**05 主体驱动：RuleDriver 与使用 Agent.iter 的 LLMDriver。** Driver 管理完整 Thread、行动回执、预算与步骤影响；SDK 管理模型节点和顺序工具分派。领域终止、必需动作和批次预算通过公开节点 API 与 Tool 包装接入，SDK 默认请求上限由原策略明确覆盖。Pydantic workspace 与自动上下文压缩保持关闭，未来具体决策模型通过现有 Driver 合同接入。

真实 SDK 的离线流试验证明：incomplete 和直接 EOF 仍可能返回工具项，后者甚至正常返回而无成功 finish reason。因此保留极小的明确成功终态检查，之后才执行工具；无需自行解析 SSE。SDK 已识别的 opaque 签名及 namespace 经 typed JSON 保存、恢复、再次发送已验证。证据见 [终态与持久消息](../../research/core-next/provider-terminal-offline-20261004/README.md)，其他提供方的映射另行验收。

## 二、数据

首版基础存储固定为 SQLite 与标准 zstd，按在线事务和不可变批次选择容器。向量检索维持已验证认知语义，避免以资源收益替代结果验证。

### 2.1 权威与正文

**06 热状态：SQLite/APSW，使用普通表、索引、短事务、Session 和 backup。** 规范写者维护事实和投影，主键与 schema 明确。大正文与热字段拆分，虚表作为可重建派生索引。现有完整步骤协议保留为有限应用协调，不新增 WAL、MVCC 或通用数据库后端接口。完整步长事务会改变运行中可见性，首版继续采用短事务加完整点发布。

**07 压缩：backports-zstd，复用原生独立帧与流。** Thread 等在线正文和不可变数据集使用同步原生独立帧；大顺序 changeset 使用原生流。应用不维护压缩工作池和 Python future 队列。原生 API 已有全值、范围和 Session 直连试验，产品 sink、故障和完整恢复按实际消费者验证。

**08 冷正文：单份不可变 SQLite 工件，原生主键索引加标准独立 zstd 帧。** 每批次共同封存记录目录与正文，记录保存原文字节起点与长度，blocks 主键直接定位至多 64KiB 原文的有限块。小记录共享块，在线单值与冷批次共用 ChunkWriter 编码；单次读作用域使用标准 functools.lru_cache(maxsize=1) 复用一个解压块，退出清空。Thread 小消息继续同库事务，大消息使用 seq/chunk 索引。统一引用、分页、原文范围和全文读取合同。真实大根反证表明 seekable 每次打开解析全帧目录，逐记录独立压缩又显著扩大空间；当前选择按批次连续压缩与 SQLite 索引，保持独立请求的短期只读连接。证据及最终资源边界见 [本轮复审](../../research/core-next/acceptance-20261004/capability-review.md)，整体收益按完整流程重新验收。

**09 JSON：直接保留 RapidJSON 流式编码。** 精确类型、插入顺序和现有正文合同继续验收；避免把整个对象先编码成一份额外 bytes。orjson 和 SQLite JSONB 不进入通用正文路径；有具体受限小消息热点时另做局部测量。数据库和文件投影都不要求将整个 World 表示成 Python dict。

### 2.2 查询与恢复

**10 查询：APSW 参数化 SQL、注册投影和小型白名单表达式。** 数据库负责筛选、排序与索引，应用负责 scope、版本、total、原文引用和继续读取。SQLAlchemy/sqlakeyset 会引入连接适配，其可删除范围只是几段拼装，首版不采用。查询语法只覆盖实际消费者，避免扩成通用 SQL 引擎。

**11 恢复：原生 Session 流＋完整描述符，保持单一完成权威。** 工件先准备、事务登记、封存变化、最后发布；恢复与只读历史准备共享物化路径。root 复制和链重放成本明确保留。同一文件系统的不可变文件使用硬链接，跨文件系统复制。在线 SQLite/WAL 使用本地文件系统，网络挂载用于封存和传输，跨机器观察通过 HTTP。Litestream 仅适合作独立灾备研究，其 LTX 边界无法直接保证每个仿真步骤；RocksDB/LMDB/Temporal/DBOS 不进入首版，避免为分叉或任务恢复的一项特性迁入完整系统。

**12 记忆检索：Chroma 为默认，sqlite-vec scalar exact 是显式后备策略。** 两者在同向量样本上的候选差异已被观察，后备不自动替换默认，也不包装成 Chroma 兼容接口。权威正文、原始向量和最终重排共用，后备通过窄候选接口进入。精确策略必须冻结在运行配置并验证主体输入和效果；首轮重构不必同时实现它。Parquet/DuckDB 不进基础安装，有真实表型分析消费者时作为独立分析插件采用。

## 三、运行

运行基础采用标准库与已经接入的成熟 shell。插件机制围绕需要共享的模拟合同保持小型，资源分区与业务顺序各有明确拥有者。

### 3.1 生命周期

**13 任务：asyncio.TaskGroup 加小型主体邮箱。** 任务组接管等待、失败取消和退出，邮箱保留同主体串行、信号合并、激活轮次和完成判定。将运行及资源清理放在组拥有的子任务中；已做外部两次取消的有限验证。行动取消后的步骤失效继续显式记录。AnyIO 保持为 ASGI 的内部依赖，不成为仿真 Core 的第二取消模型。

**14 插件主机：graphlib.TopologicalSorter 与 AsyncExitStack。** 保留名称、依赖、服务注册和两阶段关闭的小实现。schema 初始化先于服务安装，数据外键与安装 DAG 分开；Workspace 的服务依赖只有存储，Actor 驱动工厂可使用 Workspace。首版显式 Python 组合，暂无外部分发发现需求，不引 pluggy 或自动插件扫描。

**15 时间：Schedule 产生 StepPlan，Runtime 执行完整步骤。** 内置 SequenceSchedule 以明确时间序列和阶段序列覆盖固定步长及复杂阶段，阶段选择和机制函数复用已有 Python 路径。移除 FixedStep／PhasedSchedule／CodeSchedule 的重叠包装及 runner 的具体结构依赖，迁移范围见专项规格。SimPy 留给有实际离散事件需求的机制插件，墙钟网络等待与模拟时间继续分开。

### 3.2 文件与计算

**16 VFS：Bashkit＋现有窄 Rust/Python 桥＋逻辑文件视图。** 原文路径保持文件类型，显式 manifest/parts 提供附加分片入口。Bashkit 和 just-bash lazy 文件都可能整读，换 Node 解释器不能消除此成本；专用范围读取与成熟流式搜索处理常用资料访问，原生 shell 完整文件命令按实际物化计量。文件命令、路径及 OverlayFs 由成熟库负责，视图按原文版本生成，全部内容仍可取得。私有巨文件 copy-up 和整体输出成本明确计量。OpenViking 仅参考信息组织，fsspec 无远端文件消费者时不引入。

逻辑文件入口进一步收敛为 read／ls／find／grep 加 bash。传输分片与原文搜索分开：Pi 的整读工具和 Bashkit 的片目录 grep 不能直接提供跨片原文语义；ripgrep 的 Reader 搜索负责连续字节，现有信息范围接口供数。单行长度和多行搜索的内存边界、Rust/Python 回调及取消在正式接线中验收。大集合关系查询保留数据库下推和可读结果文件，避免自造 shell 查询优化器。

**17 访问策略：小型绑定 Access 与数据库角色/关系索引。** discover/read/invoke 为共同入口，机制实现领域判断。首版不引 Casbin 策略语言，因为仍需自行连接当前版本、SQL 过滤和行动资格；常见逻辑通过共享普通函数和索引复用。不会为每条记录逐次运行通用策略器来替代可下推筛选。

**18 多核：原生批量能力优先，独立 Python 计算用 ProcessPoolExecutor。** 共享写入继续单一拥有者，结果按研究语义安装；输入是紧凑批次或不可变引用。每运行统一配置进程数和在途任务数；机制另行控制每批数据字节量，任务额度本身不构成内存字节上限。Ray/Dask 不进入单机首版；只有明确跨进程任务图或远端执行需求才重新选型。

## 四、交付

外部接口与测试工具直接采用已有通用组件；应用保留运行语义和研究证据。以下选择足以承接首版外部使用，无需新增另一套运行平台。

**19 HTTP 与观测：Starlette＋Uvicorn，首版轮询与持久 tail。** 替换自有 HTTPServer/socket/线程管理，保留只读查询和最终响应预算。FastAPI 的模型层与 OpenAPI 暂无必要消费者；SSE/WebSocket 按未来需求加入。阶段计时、队列和字节使用简单数字进入已有观察服务，不引 OTel SDK/collector。诊断失败与权威写入失败分开，避免观测再次复制完整业务载荷。

**20 测试：pytest、Hypothesis 与真实 SDK 的离线 HTTP 替身。** 测试主体调用同一个公开实现；状态机负责产生取消、写入、恢复组合，传输替身负责流与提供方错误。原生库、shell 与服务都做独立消费者验收，实际免费端点保留给未来真实工具/记忆链。第三方依赖内部的通用功能由上游负责，本项目测试适配和仿真语义。

本轮不复制任何候选的大型框架源码。首选均以直接独立 API 或当前有效的小型应用合同接入；源码阅读用于证实边界、拒绝不合适的依赖并指导测试。选型在设计层收敛，实施必须保留隔离试验尚未覆盖的账户、平台、故障与整步性能验收。
