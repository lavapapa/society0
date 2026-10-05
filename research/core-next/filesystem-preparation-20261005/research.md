# 文件交互与主体插件准备研究

研究日期为 2026-10-05，产品基线为 `55d1913ca2e5cd5be84dbab0004d29580954c827`，工作树为 `codex/society0-core-next`。本轮核实当前代码、官方来源与小型反例，更新产品规格和实施计划；未修改产品代码或产品依赖锁，未调用真实模型。目标合同见 [专项规格](../../../docs/core-next/agent-filesystem-design.md)，执行状态见 [TODO](../../../docs/core-next/TODO.md)。

## 一、取舍

用户希望以熟悉的文件工具探索主体可见资料，保留 bash，并让驱动、认知与时间机制具备真实扩展能力。成熟组件的判断依据是可调用接口、实际读取行为与当前架构接入成本，避免因单个 feature 引入完整代理框架。

| 部分 | 首选 | 比较结论 |
|---|---|---|
| 文件工具形态 | read／ls／find／grep 加 bash | Pi 提供可理解的交互样式；自有适配继续绑定 Society0 的身份、版本与原文引用 |
| Shell／工作区 | 已锁定 Bashkit 与窄 Rust 桥、OverlayFs | just-bash 仍有整读和 lazy 物化成本，换到 Node 尚无净收益证据 |
| 文本搜索 | ripgrep 独立 grep-searcher＋grep-regex | Reader 已通过跨块原文试验；宿主 rg 进程正确，但有逐文档启动／管道成本 |
| 文件组织 | 现有 Information 逻辑投影，参考 OpenViking | OpenViking 的目录与资源组织可借鉴，整套上下文数据库无需进入本项目 |
| 集合计算 | 现有 SQLInformation／Query | latest、filter、sample 下推数据库，通用 shell 查询产生文件结果；不编写 shell 到 SQL 的优化器 |
| 主体装配 | 普通 Plugin 提供 Driver 工厂，标准上下文管理器组合扩展 | 不增加另一个插件注册中心；Pydantic 扩展可用于 LLM 内部，但无法覆盖未运行 Agent 的规则驱动 |
| 时间安排 | 独立 Schedule 产生 StepPlan，Runtime 执行 | 固定时间序列和阶段函数足以覆盖首期；SimPy 留给真实离散事件机制 |
| 验收 | pytest＋Hypothesis 状态机＋独立消费者 | 补齐此前仅安装 Hypothesis 的缺口；随机状态序列与确定性取消测试分别承担风险 |

以上为明确采用方向；异步原生搜索桥、正式文件工具、驱动装配和新调度消费者仍需 F03–F08 产品验收。宿主 rg 仅作对照，不同时维护两个生产搜索实现。

## 二、源码

官方源码的接口限制直接影响方案；下列来源固定到取证版本，在线 latest 文档用于解释公共 API。

### 2.1 文件工具

Pi 官方仓库已重定向到 `earendil-works/pi`。取证提交为 `200387122ca450d6387f033949423114a270b96c`。[read.ts](https://github.com/earendil-works/pi/blob/200387122ca450d6387f033949423114a270b96c/packages/coding-agent/src/core/tools/read.ts) 的 ReadOperations 可以替换 readFile，但返回整份 Buffer，正文再解码、拆行和截取。[ls.ts](https://github.com/earendil-works/pi/blob/200387122ca450d6387f033949423114a270b96c/packages/coding-agent/src/core/tools/ls.ts) 取得整个目录后排序截断；[find.ts](https://github.com/earendil-works/pi/blob/200387122ca450d6387f033949423114a270b96c/packages/coding-agent/src/core/tools/find.ts) 可替换 glob，默认调用 fd；[grep.ts](https://github.com/earendil-works/pi/blob/200387122ca450d6387f033949423114a270b96c/packages/coding-agent/src/core/tools/grep.ts) 仍启动宿主 rg，其可替换 readFile 主要服务上下文读取。因此复用其工具形态，避免为这几项接口引入 Node 进程及完整 coding-agent 包。

Bashkit 固定源码 `567511386572d4c4b9f649ab1da97aad53b9f341`。[SearchCapable](https://github.com/everruns/bashkit/blob/567511386572d4c4b9f649ab1da97aad53b9f341/crates/bashkit/src/fs/search.rs) 已提供搜索扩展，但 [C ABI](https://github.com/everruns/bashkit/blob/567511386572d4c4b9f649ab1da97aad53b9f341/crates/bashkit/src/interop/fs.rs) 未传递该能力。[grep 索引路径](https://github.com/everruns/bashkit/blob/567511386572d4c4b9f649ab1da97aad53b9f341/crates/bashkit/src/builtins/grep.rs#L983) 仍整读命中文件，空命中可进入普通扫描。这个接口不足以证明范围成本。

just-bash 固定源码 `7537a260e38648e8998db7a95504102ad80b0194`。[IFileSystem](https://github.com/vercel-labs/just-bash/blob/7537a260e38648e8998db7a95504102ad80b0194/packages/just-bash/src/fs/interface.ts) 仍提供整文件读取，[lazy 实现](https://github.com/vercel-labs/just-bash/blob/7537a260e38648e8998db7a95504102ad80b0194/packages/just-bash/src/fs/in-memory-fs/in-memory-fs.ts) 首次读取物化，stat 也可能为取得长度而物化。本轮没有通过更换解释器消除该成本的证据。

OpenViking 固定源码 `9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14`。[FsService.read](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/openviking/service/fs_service.py#L1083) 先取全文再切行，[localfs 读取](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/crates/ragfs/src/plugins/localfs/mod.rs#L837) 先 fs::read 再取范围。文件式资源组织值得借鉴，该实现不直接解决巨正文读取成本。

### 2.2 搜索与运行

ripgrep [Searcher.search_reader](https://docs.rs/grep-searcher/0.1.17/grep_searcher/struct.Searcher.html#method.search_reader) 接受标准 Reader，由成熟搜索器处理连续原文和结果 Sink。逐行搜索的缓冲可能随最长行增长，多行模式可能整读；[heap_limit](https://docs.rs/grep-searcher/0.1.17/grep_searcher/struct.SearcherBuilder.html#method.heap_limit) 是近似搜索器堆预算。完整试验、固定依赖与统计口径见 [流式搜索报告](../stream-search-preparation-20261005/README.md)。

SQLite [FTS5](https://www.sqlite.org/fts5.html) 的 trigram 与 BM25 已由当前 Actions 调用。它索引行动模板名称和说明，按目标类型及权限选择当前可执行动作；不索引全部世界事实，也不决定主体应执行什么。中文短词和语义同义词的匹配能力不能从 BM25 的存在推出。

Python [AsyncExitStack](https://docs.python.org/3/library/contextlib.html#contextlib.AsyncExitStack) 和 [TopologicalSorter](https://docs.python.org/3/library/graphlib.html) 已承担现有资源组合和依赖排序。Pydantic [Capabilities](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/) 围绕 Agent 生命周期，适合 LLM 内部。通用主体扩展使用标准异步上下文管理器，不要求规则驱动进入模型框架。

[SimPy Environment](https://simpy.readthedocs.io/en/latest/topical_guides/environments.html) 管理事件队列与离散事件时间。当前阶段序列与主体激活无需引入整个队列系统。[Hypothesis stateful](https://hypothesis.readthedocs.io/en/latest/stateful.html) 能生成操作序列并缩减反例，适用于工作区、版本、发布和恢复组合；它不替代真实线程／进程取消的屏障测试。

## 三、试验

本轮使用小型确定性输入，不产生大型运行工件。以下结果用于识别设计前提及选择组件，不能推断整套仿真的吞吐、内存或模型效果。

### 3.1 分片反例

[grep_projection_probe.py](grep_projection_probe.py) 使用真实 Bashkit、SQLInformation 和当前 InformationFiles。1272 字节正文中的“甲乙”位于 768 字节分片边界，两字分别保持完整 UTF-8；递归搜索文本片返回零命中，拼接后返回正确命中。第二条 1104 字节正文的递归搜索同时经过 manifest、文本和 base64 三种表示；原文不存在的 QUFB 可以命中 base64 副本。原始结果见 [grep_projection_result.json](grep_projection_result.json)。

由此确定：传输分片不承担逻辑搜索身份。统一文件工具须对原文连续读取，逻辑遍历不得重复搜索传输元数据与编码副本。这个变化需要正式原文／分片两个明确入口，不能简单改名旧 data_read。

### 3.2 成熟搜索器

Rust 探针对每两字节切分的中文和跨 64 KiB 块的关键词均正确匹配。分别搜索三个文档保留边界，命中数为 0、0、1。1 MiB 单行的搜索期峰值堆增量为 1,777,680 字节；4 MiB 短行流为 73,744 字节。计数是隔离 GlobalAlloc 申请统计，不代表进程 RSS。64 KiB 搜索堆限制遇到长行时明确报错。

宿主 rg 的 stdin 路径也正确，30 个小文档逐进程调用的本机中位约 4.76 毫秒，包含启动、管道和搜索；该样本不构成完整产品性能对照。Rust 库可以复用已编译 matcher 和 Searcher，因此选其独立 Reader 接口进入现有原生桥。输出直接写既有结果工件，避免聚合全部命中。

搜索器在首行命中后停止时已经预读 64 KiB，Reader 的读取位置不能当作已处理位置。首期完成搜索后分页读取结果文件，避免引入另一套恢复扫描状态机。真实 PyO3 异步回调、取消和源数据范围成本仍需接线验收。

### 3.3 原文路径接线

[logical_file_probe.py](logical_file_probe.py) 通过真实 Bashkit 和现有 callback_filesystem 桥将 `/world/document.json` 映射为普通文件，正文 1,048,576 字节，关键词从 65,530 偏移开始跨过 64 KiB。cat、head -c 32、grep -o -F 和 jq -r .marker 均返回正确结果，每条命令均调用一次 read_file 并取得完整 1,048,576 字节。cat 与原文逐字节相等；同一 SQLInformation 正文的范围请求 offset=65530、size=64 返回精确 64 字节及正确继续位置。见 [原始结果](logical_file_result.json)。

这证明同一路径保持原文文件语义的接线可行，也证明 head 输出短不能推导输入少。测量是提供者向桥返回的字节，未包含磁盘 I/O、复制次数和 RSS；正式 read／grep 的异步桥、权限、版本与恢复仍由产品消费者验证。

## 四、落地

当前 Driver 的插件化发生在 actor_plugin 工厂，RuleDriver／LLMDriver 也可直接构造。下一轮让正式运行明确引用 Plugin 提供的 Driver factory，恢复重建工厂并保留主体配置；认知生命周期外提后，规则与 LLM 可以消费同一扩展。

现行 FixedStep 仅创建一个 Phase，并不拥有固定时间间隔；PhasedSchedule 再绑定成 CodeSchedule，RunPlan.moments 才提供时间。runner 又读取 schedule.runtime/phases，限制自定义调度。目标将 Schedule 的计划选择和 Runtime 的执行／发布分开，并使用没有这些私有字段的第三方消费者验证。

独立准备审查要求补齐同路径 Bashkit 成本、跨文档搜索一致性、结果撤权与原文版本语义，以及未被召回记忆的发现入口。规格已经给出相应合同和失败先行验收项；真实 Bashkit 原文路径试验另存本目录。原生 shell 的整文件成本明确保留，不能由专用 read／grep 的范围成本推导它已经获得流式执行。

准备阶段结束后按 F03 先建立失败消费者，再推进插件与文件入口实现。广泛比较已收敛到明确选择，后续试验集中验证这些选择的接入边界；发现不能满足正确性或资源约束时，先回到该合同修正，不沿失败路线叠加自有算法。
