# 新版 Core 实施待办

本清单管理本次从能力盘点到完整交付的工作。产品合同见 [PRD](PRD.md)，旧版有效能力与证据见 [能力对照](capability-parity.md)。实施顺序遵循本节最新用户安排；实验成功、代码完成和产品验收分别勾选。每个完成项需记录代码、测试或报告位置，未实现项保持未勾选。

## 一、约定

### 新人交付与实际提供方验收（2026-10-04）

用户已授权按新人审查推进，指定 SiliconFlow `Qwen/Qwen3.8-27B`，同提供方选择嵌入模型。该明确授权用于本轮短程测试，替代此前本轮只能使用免费入口的限制；DeepSeek 仍禁用。密钥仅由进程环境传入。缓存需核实提供方实际接口和命中证据；模型调用协议与仿真语义分别验收。

- [x] **D01 新人入口与恢复**（beginner_audit）：修复现行状态、旧示例与结果读取冲突，完成无模型起步、分析与恢复教程，连续/恢复时间与结果对照。证据：[规格与红绿验收](../../research/core-next/onboarding-20261004.md)，新增三项消费者及相关 24 项通过；最后整体验收归 D04。
- [x] **D02 Agent 复用审查与落实**（agent_runtime_audit，独立 delivery_review_sol）：正式采用 Pydantic AI `Agent.iter` 与标准工具分派；保留领域回执、预算、失败边界及完整 Thread。默认动态参数改为原生 JSON，显式 strict 模式保持单独的文本合同；分页保留提供方任意 JSON 游标。修复每激活与记忆提取的重复历史驻留。证据：[研究与最终合同](../../research/core-next/agent-runtime-study-20261004/report.md)、97 项相关消费者及独立长历史、记忆所有权、多段 SDK 流式用量测试。适配净增代码的成本与仍保留的记忆提取协议均已明确，最终组合验收归 D03、D04。
- [x] **D03 提供方与目标验收**（主控、storage）：指定 Qwen 与同提供方 1024 维嵌入已实际运行，十五个唯一场景有明确版本与结果映射；VFS 精确原文 oracle 修正后使用同一断言复核原工件，未追加模型请求。累计流用量采用 SDK 现成配置纠正，已报告模型输入中的缓存读取约 23.22%；恢复共同历史按物理调用身份去重。相同完整点的召回关闭/开启对照分别得到“资料不足”和正确 B42/500 回答；仅支持本样例可用性，不作为统计效果结论。固定活动量增长负载另有规则试验。证据：[真实验收及剩余边界](../../research/core-next/provider-acceptance-20261004/final-report.md)、[规则规模试验](../../research/core-next/provider-acceptance-20261004/rule-results.md)。全部失败、超时和早期失真计量保留。
- [x] **D04 交付身份与独立验收**（独立 GPT‑6.1 Sol，主控）：最终 candidate 在 macOS、Linux 各通过 808 项主测试和 75 项实验，Node 真实产物 9 项通过；干净 wheel 的 47 个产品文件与源码逐字节一致，外目录基础安装、结果读取和恢复通过。实现提交 `a452b32b4c96be4295dc42f8da6d3d38a512fc49` 已推送 `codex/society0-core-next`；229 个源码、测试、原生源码及依赖文件与最终测试归档直接比较相等。证据：[独立交付审查](../../research/core-next/delivery-acceptance-20261004/review.md)、[提交身份](../../research/core-next/delivery-acceptance-20261004/delivery-identity.json)。本轮候选交付已完成，Codex 订阅真实账户与长期效果门继续保留在 R16，未作 PyPI 发布。

任务 ID 在实施中保持稳定，负责人可以调整。依赖表示开始或验收所需的输入，允许不争用同一文件且无语义依赖的任务并行。阶段内某个原型完成不代表整阶段完成。

当前工作树为 `codex/society0-core-next`，合并基线 `738ba70cf7881649c38422d4b3f00ffed12ab51c`。能力盘点由 spec 负责；产品主机与调度由 threads 按主智能体分工推进，存储/数据实验由 storage 负责，主智能体统筹 PRD、SDD 与整体交付。真实验收按当前用户授权及具体运行合同执行；密钥仅通过进程环境使用。I03 的确定性基线与 V05 的真实服务验收分别记录。

2026-10-04 用户要求收敛技术选型，核实订阅 OAuth 接入，重新设计整体框架并完成 PRD 后停留在设计阶段。该轮完成 R05–R07，隔离试验记录为选型依据。后续用户授权与当前执行边界见下段。已有通过项保留历史证据，受替换影响的验收须在未来重跑。DeepSeek 不再用于测试；其他真实请求须确认具体账户和模型免费，额度耗尽时等待或结束，无收费 fallback。本轮设计核查不登录账户或发真实模型请求。

上轮按用户要求完成 R08–R12 代码并在 R13 测试前停止。当前用户已授权全部测试及必要修复，指定独立 GPT‑6.1 Sol 执行，主智能体监督根因、范围和成熟组件使用；本轮按 R14–R17 推进。首版订阅支持 Codex/ChatGPT 计划；外部返回完整 Responses 结果，必要的流式传输由提供方适配内部消费。真实调用沿用免费端点约束，禁用 DeepSeek 和收费回退。

- [x] **R14 测试盘点与环境**（独立 acceptance_sol）：锁定本树依赖与实际导入身份，核对 78 项能力、所有测试目录、工作台和原生扩展；建立失败分类与执行清单。证据：[本轮验收记录](../../research/core-next/acceptance-20261004/status.md)、[78 项能力复审](../../research/core-next/acceptance-20261004/capability-review.md)。确定性、真实服务、实验候选和平台门分别记录；最终安装与运行验收继续由 R16 承接。
- [x] **R15 直接测试与修复**（独立 acceptance_sol，主智能体监督）：受影响模块先失败用例，再最小根因修复；完整消息、模型终态、存储恢复、取消、VFS、HTTP、多核和公共入口均保留行为断言。用成熟库承担通用职责，不引新自有算法/结构或兼容层。产品在 2026-10-04 14:59:13（台北）冻结，直接消费者与错误注入已通过；修复与原断言承接见[测试迁移复审](../../research/core-next/acceptance-20261004/migration.md)。最后全量、平台与规模测量继续归 R16。
- [ ] **R16 全量与实际消费者**（独立 acceptance_sol；本轮后续见 D03、D04）：前轮共享帧源码在 macOS 与 Linux 各通过 838 项（763 项主测试、75 项实验），15 项真实服务用例分别排除。SQLite 索引共享 64 KiB 原生 zstd 帧在同源 1,448,471 条记录上逐值恢复相等，物理空间与旧基线同为 226,680,832 字节，完整流程耗时同量级，1,000 次独立小页读取为 0.213 秒；历史统计见[执行摘要](../../research/core-next/acceptance-20261004/execution-summary.json)。D03 已补齐指定 SiliconFlow 的十五场景组合验收与单次召回对照，D04 记录最后源码的双平台、安装及工作台验收。Codex 订阅实际账户、复杂机制长期负载和有统计依据的主体效果仍未完成，本项继续保留未勾选。
- [x] **R17 独立复审与结论**（主智能体组织另一位 GPT‑6.1 Sol）：非产品作者复审最后源码、测试迁移、78 项能力映射与最终平台／规模工件，未发现当前冻结产品的新阻断问题；原信息、权限、失败和恢复语义保留，没有新增自制调度器、索引或压缩算法。结论与真实服务、主体效果、完整业务长期负载的剩余边界见[本轮能力复审](../../research/core-next/acceptance-20261004/capability-review.md)。

- [x] **R08 模型与订阅实现**（spec）：Pydantic AI 低层模型/嵌入、Authlib SIWC 登录与刷新、完整消息持久化及成功终态、资源计数和请求许可；替换旧协议实现，接通公开工厂。
- [x] **R09 存储实现**（storage；验收修正由 acceptance_sol）：原生 zstd 在线正文、SQLite 索引的不可变冷批次、Session 压缩流、完整点/恢复/导出/清理实际消费者；删除自有压缩任务池。本轮真实大根试验发现 seekable 容器每次读取重建全量索引，已改用 SQLite 按记录与块定位原生 zstd 帧；最后源码与性能验收归 R16。
- [x] **R10 运行与外部访问**（threads）：TaskGroup 任务树、插件生命周期、Bashkit 有界文件投影、Starlette/Uvicorn 与公开 CLI。
- [x] **R11 集成与公共入口**（主智能体编排）：动态行动检索、公共插件与阶段接口、标准库多核计算资源、依赖锁、示例和安装/使用文档同步；所有选定职责有实际消费者。
- [x] **R12 独立静态审查**（非作者交叉审查）：读取最终差异和调用链，处理接口、取消、恢复、凭据与部分响应的可见问题；本项不执行代码或测试。
- [x] **R13 测试前停止**（主智能体）：记录实际源码规模、修改范围、未验证事项和测试入口；不将代码完成标为产品验收，不部署或发布。 证据：[本轮实施与静态审查](../../research/core-next/implementation-pretest-20261004.md)，R08–R12 已完成；库实际 11,276 行，超过原预算上限，全部新路径运行验收留待下一阶段。

- [x] **R05 明确选型与可行性证据**（主智能体+三位子智能体）：每个职责给出唯一首选及必要后备，区分直接依赖、源码参考和不采用；核对 Pydantic AI/LiteLLM/pi-ai 的 Codex OAuth 与其他 coding plan 支持；以隔离试验证明关键 API 和资源边界。证据：[明确选型](technology-decisions.md) 及链接的模型、存储、运行报告，补充 [SDK 终态](../../research/core-next/provider-terminal-offline-20261004/README.md)。实际账户与整套产品仍待未来验收。
- [x] **R06 架构与规模论证**（主智能体，非作者审查）：确定 Core、成熟库适配、内置插件、机制插件及外部接口的单向依赖；按现行源码计数估算全部自有代码，列保留/替换/删除责任与能力覆盖。证据：[规模与覆盖](size-and-coverage.md)，同口径库基线 12,736 行，目标约 8,000–10,500 行，全部 78 项能力重新验收。
- [x] **R07 PRD 与设计验收**（主智能体，非作者审查）：更新权威 PRD、选型记录和测试设计，写明先前实测与新计划的区别；文档交付后停止，不启动产品实施。证据：[PRD](PRD.md)、[独立设计审查](../../research/core-next/design-selection-review-20261004.md)；两位非作者审查完成，依赖环、响应终态和文件系统部署条件均已处理。

- [x] **R01 经典问题盘点**（主智能体+三位子智能体）：按现行源码识别协议、存储、压缩、查询、并发、VFS、插件、实时服务及测试基础设施的自有职责。证据：[二十项问题与取舍](../../research/core-next/mature-components-review-20261004.md)。
- [x] **R02 成熟方案调研**（主智能体+三位子智能体，依 R01）：核对官方资料、发布身份、替代范围、成本和未验证边界，形成首选、对照及条件候选。证据：上述综合研究及其链接的三份专题研究；候选性能尚未实测。
- [x] **R03 小型选型试验**（依 R02，范围由 R05 收敛）：已完成模型/OAuth 离线接口、流终态、原生 zstd/seekable、Session、TaskGroup/AnyIO、Bashkit/just-bash、ASGI 最小生命周期及向量对照。Parquet/DuckDB 经消费者分析退出首版，不再为候选完整性增加试验。证据见 R05 三份专题与原始工件；Linux、真实网络、账号和产品组合属于 R04 后续验收。
- [ ] **R04 替换与重新验收**（依 R03；实现见 R08–R12，测试见 R14–R17）：将选定责任交给成熟库，删除被替代实现，更新 PRD/SDD、依赖和能力矩阵；相关测试、完整消费者、独立审查及最终全量通过后再推进交付。

完成一项时，在原条目追加“证据：路径/测试名/报告”，记录实际结果及限制。对应新语义尚未实现时，旧版测试通过不能将其标为完成。出现 provider 等待保留任务进行中；错误修复须先定位影响范围和可信恢复边界。

## 二、P0 试验

这一阶段交付可执行的选型依据。插件依赖/清理原型与正式 Core 主机具有独立任务及验收；原型可暴露接口缺口，不能直接代替产品测试。

- [x] **I01 能力盘点**（spec，无前置）：核对基线 `96b1f3b`、main `7031b06` 与合并 `738ba70`，按行为映射有效能力并索引全部基线测试文件。证据：[capability-parity.md](capability-parity.md)，源码及测试静态读取；新版实现仍待后续任务。
- [x] **I02 main 增量归类**（spec，依 I01）：研究者 skill、完整 pilot、工作台与测试单列 U01–U05；拒绝把分支差异中的已废弃执行器当新功能。证据：[能力对照 1.1 与 5.1](capability-parity.md#11-身份与证据)。
- [x] **I03 确定性旧版基线采集**（spec，依 I01）：显式 `PYTHONPATH=当前工作树/src`，运行合并基线已跟踪 primary/e2e 文件与 `-m 'not real_e2e'`。证据：[修前基线](../../research/core-next/baseline-tests-before-skill-fix.txt)，669 passed、1 failed、14 deselected、34.28s；失败为 skill 改文案后的旧逐字断言，修后结果见 I03a。首轮共享 venv 子进程导入旧 main 的 10 项额外失败保留于 [initial](../../research/core-next/baseline-tests-initial.txt)。默认未含 performance/Node，本轮新增测试未混入。
- [x] **I03a 基线文案断言修复**（spec，依 I03）：保留 skill 可见实验 todo 与简短研究者进度的有效合同，修改 `test_society0_skill_requires_visible_experiment_todo_list` 过时句子断言。证据：[直接复验](../../research/core-next/baseline-skill-fix.txt)、[基线全量](../../research/core-next/baseline-tests.txt)：670 passed、14 deselected、32.96s；没有真实请求，未修改产品运行语义。
- [x] **I04 SDD 与验收绑定**（主智能体，依 I01/I02）：共享 Environment、公共交互、调度、SQLite/文件发布、Thread/记忆及失败合同见 [SDD](SDD.md)；全部 capability ID 的责任模块及预定新测试见 [实施验收映射](acceptance-map.md)。接口与旧格式破坏兼容已明确。此项完成规格与分工，实际逐项对标仍由 V01 验收。
- [x] **X01 插件依赖与清理原型**（threads，无前置）：标准库拓扑与 context manager，先写缺 required、环、服务冲突、A 成功 B 半初始化失败、反向清理的失败用例；证明发生副作用前校验及提供者半失败责任，记录原型代码/红绿结果。证据：`tests/primary/test_kernel_plugins.py`、`test_kernel_plugins_review.py` 与 [作者红灯](../../research/core-next/plugin-host-red.txt)/[独立红灯](../../research/core-next/plugin-host-review-red.txt)/[独立复验](../../research/core-next/plugin-host-review-green.txt)。作者 14 + 独立 6 项共 20 项通过；两项独立发现（可变依赖、清理异常被其他资源抑制）已修复。该项不表示 K01/K02 的运行集成完成。
- [x] **X02 信息与动作最小原型**（spec，独立审查 threads）：同环境订单与消息、主体绑定、discover/read/invoke、目标模板、执行重验、JSON 游标和实际版本已验证。证据：`kernel/interaction.py`、作者与非作者 `test_kernel_interaction*.py`、`interaction-shell-independent-green.txt`；真实大集合查询提供者继续归 K04/P04。
- [x] **X03 存储权威与混合发布原型**（storage）：比较原生 SQLite 事务、JSONL 准备发布、长 reader 及 SQLite Session 根+增量，验证独立进程退出后的恢复边界。证据：[报告](storage-experiments.md)、`storage-session-green-20261004.txt` 20 passed；正式 Thread/Memory/长期分叉继续归 P02/P03。
- [x] **X04 大集合与热工作集原型**（storage，依 X03 初步）：固定活动量扩大历史，测定位、分页、range、typed 值/键、顺序、根载入与 schema 验证；小 doc 与大 dataset 分开；比较扫描/物化字节和 RSS，检查 SQL N+1 及 `.items()` 全量物化。真实旧根单次转换已得到 1,448,471 条声明记录；独立新进程的固定热读无需构造旧 World，原始结果见 `v04-read.json`、`v04-diag.json`。转换、正常运行和恢复的峰值分别记录。`a2e8b5c` 已独立逐值核对全部 1,448,471 条记录及顺序；真实分离样本比较逐记录字典与分组压缩，64KiB 分组降低空间同时增加随机读解压量，见 `large-root-codec-windows-64k-20261004.json`。`0ef012a`/`4ebc725` 已验收正式冷批次全部记录及顺序、独立进程固定热读和小投影；`8369abb` 进一步验证 20MB SQL 正文引用的原生/Python 内存和零额外 rowid 查询。原型闭环，综合多核与完整驱动工作负载留 V04。
- [ ] **X05 VFS 与自主分析原型**（threads 试验，spec 产品）：Bashkit 五项选型试验和 shell 产品读写/管道/完整文本输出/原文字节通路已通过；见 [试验](shell-experiments.md)、[合同](shell-contract.md)。懒文件整读与原生非法 UTF8 替换边界已明确；SQL 数据集的 tail/sample 与完整模型任务对照待接入。[工作区试验](workspace-experiments.md) 先重现 8MiB 未改私有文件十次激活产生约 84.23MB 快照，再以原生 OverlayFs 将十次新增正文降为零。`47a3377` 已接动态 `/world` 与 SQL 路径索引，真实 LLMDriver 确定性消费者恢复文件及 shell 状态。完整模型任务效果对照留 V06；显式整文件读和整目录枚举的成本见 [工作区合同](workspace-contract.md)。
- [x] **X06 Driver/时间原型**（threads，独立审查 spec）：规则与假驱动、顺序阶段及显式独立并发、同 moment 游标、信号合并、waiting/incomplete、整步预算已验证。非作者加入真实 PluginHost+StageStore 双机制与恢复，作者/非作者共 21 项通过，见 `runtime-review-green.txt`；LLM 实际循环继续归 D02。
- [x] **X07 OpenViking 复用判断**（指定 owner，无前置）：依主智能体研究核对 URI/目录/原文/摘要/检索资产、依赖成本与恢复边界；输出直接依赖、适配或借鉴的证据。不得用摘要默认裁剪 Thread。证据：[官方源码研究](openviking-study.md)，已核对版本/读取成本/快照/权限/依赖并建议 URI/View 借鉴与可选 SDK；直接集成与性能原型尚未实施。
- [x] **X08 有界多核小实验**（storage，依有明确块输入）：压缩线程、纯 Python 计算进程与串行，对相同完整数据逐值核对，计墙钟/CPU/在途字节/进程 RSS/IPC；与产品并行接入任务 D07 分开。`380a7a1` 完成 32 核服务器串行/4 线程/4 进程同值试验：原生编码压缩墙钟 1.035/0.308/0.391 秒，纯 Python 数值计算 0.497/0.504/0.259 秒；进程启动、输入/输出 IPC、CPU 与进程组采样 RSS 已计入。正式 Runtime 的规则、LLMDriver、Memory 三路径各跑 20 步并恢复逐值比较，模型返回使用确定性替身。作者 9+3 项与非作者 9+3 项通过，计量独审无阻断；内存与计时重叠边界见 `x08-runtime-parallel-20261004.md` 和 `x08-spec-review-20261004.md`。
- [x] **X09 P0 独立审查与取舍**（非作者，依 X01–X08）：指出原型未覆盖的故障与语义，确定首版实现方案并更新 SDD；仅选择已有消费者支持的公共接口，保留失败实验数字。各 P0 试验已有作者和非作者证据，SDD、语言决策及现行模块合同明确 SQLite/原生库、文件级工作区、插件组合的取舍；最终 78 项复核及温度边界独审没有其余实现缺口。证据见 `capability-review-20261004.md`、`final-review-r2-20261004.md`；真实效果和最终源码总复验仍分别归 V06/V08。
- [x] **X10 实现语言选择**（主智能体，用户新增要求）：Python 公共 Core/插件接口，成熟 C/Rust 组件处理数据与重计算，TypeScript 用于工作台；新增 Rust 扩展由实测热点触发。证据：[语言决策](language-decision.md)、原生压缩与 SQLite Session 试验。整步性能验收继续归 V04。

## 三、P1 主机

这一阶段形成可由用户调用的最小产品，承接 P0 结论。一个共享环境中的两个机制插件是最低集成消费者，普通单机制和规则主体仍有短路径。

- [x] **K01 Core 插件主机 TDD**（threads，依 X01/I04）：正式主机与 runtime_plugin 通过真实持久化双机制消费者集成；依赖、实例、冲突、环、清理及无全局 registry 已验。证据：`test_kernel_plugins*.py` 和 `test_kernel_runtime_review.py`，入口使用独立 Core 服务，无旧 World 依赖。Actor/LLM 全能力继续归后续任务。
- [x] **K02 生命周期与运行 scope**（threads，依 K01）：按依赖启动、反向关闭、部分初始化失败、取消、总资源预算、task-local actor 绑定与失效；普通异常清理和业务回滚分别验收。由 spec 独立审查 X01 与产品差异。主机关闭、失效 scope 与消费者已贯穿。`2aaf155` 增加运行共享原生 Semaphore，模型及嵌入在必需留证/SDK 前取得物理许可；完整 Thread 仅获许可后按固定水位装载，排队与退避释放正文。98 项作者/相关测试和 65 项非作者复验通过，覆盖跨 profile 额度、排队取消零物理记录、快照失败释放、重试全文同值及无强制 gc 的引用释放。压缩池随 Store 共享；缓存按显式 profile 字节限额，Chroma 原生索引的独立容量边界见 V04。证据：[请求许可合同](request-limit-design.md)、`request-limit-final-reviewed-candidate-20261004.txt`、`request-limit-independent-20261004.txt`。
- [x] **K03 Actor/Object/ResourceRef**（spec，依 K01）：身份/类型/实例、主体主观状态与领域角色事实、对象引用按需解析；保持值精度/类型/顺序，禁止复制整个环境到每主体 runtime。证据：compose 的 Plugin schema/initialize（`ce83ef2`）与 ActorStore/Runtime 懒解析（`f7b4a42`）；`test_kernel_actors*.py`、`runtime-actor-independent-green.txt`。冷热分表、一级主观状态局部更新、角色索引和精确计数已验证；`63675b8` 进一步使用按主体版本绑定的 ActorRecordView，激活先读短头，persona/config/state 按访问读取，state_values 可选字段。作者 23 项相关验证与非作者 4 项复验覆盖同一短快照版本检查、无关主体修改、恢复和固定两键读取；认知明确请求全部资料时仍承担完整材料成本。
- [x] **K04 View 与 Access**（spec，非作者 storage/threads，依 X02）：Information 路由与 SQLInformation 提供主体绑定、授权数量、固定版本游标、原生 BLOB 范围和索引分页。真实两回合 LLM 组合发现的 Thread 写入使下一页失效问题已修复，`b60b7aa` 按查询与权限依赖表维护事务版本。84 项相关用例包含无关记录不打断、领域或权限变化失效、回滚、跨运行游标及 Actor 选择续页；其中 6 项由非作者从实际 LLMDriver 验证。证据：`test_kernel_domain_revisions.py`、`test_kernel_llm_paging_review.py`、`domain-revision-owner-green.txt`。单次外部传输总字节预算继续归 Q03。
- [x] **K05 动态 Action 与 Intent**（spec，非作者 threads，依 X02）：模板、find/describe/invoke、schema、执行重验、outcome、相关依赖版本与权限变化失效已实现。`87c15e5` 将策略过滤下推到一次枚举，10000 模板的可用性检查从 1000000 次降至 10000 次，持久版本游标不再携带全部候选名；无版本的纯内存路径保留候选校验。证据：`test_kernel_action_discovery_cost.py`、`test_kernel_llm_paging_review.py` 与 `model-selection-discovery-independent-green.txt`，47 项相关独立复验通过。
- [ ] **K06 workspace 与 LLM 交互适配**（指定 owner，依 K04/K05/X05）：私有资料读写查删、lazy VFS、完整原文/data query/分页/总数/tail；写世界经过 action，规则驱动直接用结构化服务。`47a3377` 已用共享 WorkspaceStore 和薄 Rust/Bashkit 桥替换 Actor 整包工作区接口：未改正文复用、文件级变化、删除/重命名/元数据、原文 Ref、只读共享挂载和恢复通过；作者最终 128 项相关测试及独立 67 项组合通过。8MiB 文件十次未改激活新增正文为零，1000 主体仅按需创建一个 shell，50k 目录显式枚举约149ms/26MB。`3ba1cbc`、`d8a73a4` 完成 macOS arm64/Linux x86_64 wheel 真实文件恢复与独立基础安装；纯规则路径未安装模型/向量/shell。V06 真实模型自主分析及最终发行继续验收。
- [x] **K07 规则 Driver 与贯穿样例**（spec，依 X06/K04/K05）：`examples/core_next/shared_environment.py` 使用 PluginHost、StageStore、Runtime 与两个规则主体，实际完成 SQL 查询、抽样、jq 分析、动态行动和消息读取；封存输出与 workspace 后恢复逐值相等。证据：`test_kernel_information_sql.py` 中完整例及 `information-sql-green.txt`。跨 LLM 激活的 workspace/回执消费继续归 K06/D04。
- [x] **K08 P1 独立审查**（非作者，依 K01–K07）：角色伪造/范围泄漏的语义负例、失效对象、执行时条件变化、partial write 与资源累计、懒加载实际分配；按项目约束做能力正确性审查，不开展额外安全性研究。 `2646feb` 修复第一轮目录发现和部分行动误发布两项阻断；`2590cf3` 精确快照的第二轮非作者源码审查无新增产品阻断，并新增并行主体故障失效及独立恢复用例。证据：`final-review-r2-20261004.md`。

## 四、P2 对标

这一阶段迁移全部有效运行能力。每组标注能力 ID，完整旧版行为逐项获得新入口证据；若接口改变，使用新接口表达等价任务。

### 4.1 主体与模型

- [ ] **D01 Provider 插件**（threads，非作者 storage，依 K02）：R01–R05，多模型/端点/并发/timeout/取消/retry/trust_env/session、strict/parallel/tool_choice、embedding 维度/微合批。`dc6f558` 已接完整 profile 配置、物理原文一次保存、逻辑逐项与失败重试来源、缓存条数/估算字节上限及关闭排空。作者与旧资源资产 49 项、独立 28 项通过，四项独立发现已回归；见 `models-lifecycle-green.txt`、`models-storage-independent-final-20261004.txt`。`2aaf155` 已接共享物理请求许可，完整上下文按需装载与排队/取消/重试生命周期已独立验收。真实 embedding 适配器已小样成功，最终统一源码下全提供方合同与真实 LLM 链继续验收。
- [x] **D02 LLM Driver 基础循环**（threads，非作者 spec，依 K05/D01）：动态元工具、行动账本、结构化测量、terminal/required、完整 trace、persona/View/认知与记忆已接通。`87c15e5` 补齐 A07 的单次覆盖→主体→类型→默认模型选择，复用原共享提供方并保同一 Thread/session；reasoning_stages 保持单次请求提示与原文解析，结果用消息和字符范围引用。独立发现的标记格式缺失、复制巨正文已回归；证据：`test_kernel_model_selection*.py`、`model-selection-discovery-independent-green.txt`，作者组合 90 项与非作者相关 47 项通过。真实提供方往返与最终跨插件验收继续归 V05/V07。
- [x] **D03 LLM 预算与失败组合**（threads，非作者 spec，依循环实现）：L05–L10 的总/逐 action/turn 预算、失败尝试、重复 call、parallel false、length/empty/schema/provider 错误已通过作者及独立测试。三个独立发现（物理请求水位漂移、诊断重复复制历史、恢复终止回执后多请求）已回归；见 `test_kernel_llm*.py`、`test_kernel_models_review.py` 和 `llm-manager-regression-green.txt`。真实服务与记忆组合留在 V05/D05。最终 V01 核对补齐 L09/L10 可选温度策略，`89ad172` 保留原请求基值、空响应预算与连续重复事实合同；65 项作者及非作者组合通过，包含实际 Provider 默认值、物理重试、早退重置与同轮新事实优先。默认关闭，完整历史及预算不变。
- [x] **D04 完整 Thread 与续激活**（storage/threads，非作者 spec，依 D02/K02）：ThreadStore 的分块原文、请求水位、tail/range 和完整恢复已通过作者与独立测试，见 [Thread 合同](thread-contract.md)。持久工具回执、跨进程同 moment 定位和 shell 旧产物消费已集成；`f7b4a42` 增加工作区恢复和固定记忆输入水位，`f273b15` 已将认知输入与消费游标原子发布；`cf08abc` 完成 CognitiveInput、变化背景追加、A→B→A 和独立进程恢复。证据：`test_kernel_threads*.py`、`test_kernel_cognition*.py`、`cognition-memory-integration-green.txt`，相关 99 项通过。长 Thread 不裁剪；真实提供方端到端继续归 V05。
- [x] **D05 记忆插件与三个开关**（storage，依 D04/D01）：M03–M09；逐一验证召回/提取写入/主动记忆工具 2³ 组合、interview 默认测量、budget/length 无成功记忆、提取失败/空选择/同 Thread 重试/single-flight/pending→write→receipt。同一有效作业重试避免重复提取；完整步骤之后、崩溃前发生的外部调用可能重算，不从 dirty 诊断偷偷恢复。证据：`cf08abc`、`test_kernel_memory*.py`、`test_kernel_cognition.py` 默认真实组件组合；31 项 Memory 作者/独立用例及 99 项相关组合通过，含访谈默认不提取、长度失败不生成记忆、提取纠正和回执恢复。独立逐项对照发现的逐激活策略与业务日期耦合缺口已由 `0ef012a` 修复：同一 Memory 服务按实际 Session scope 冻结三个开关与 top_k，主动工具沿同一策略，退出后旧绑定失效；Session.step 作为默认可见与衰减序号，Moment.time 保留业务原文。163 项作者组合、20 项非作者复验及最终 516 项 Core 联合通过，覆盖双主体、同主体重复激活、日期与非连续业务时间、恢复及权限作用域。证据：`memory-step-integration-green-20261004.txt`、`memory-activation-independent-green-20261004.txt`；最终真实资源链仍由 D01/V05 验收。
- [x] **D06 记忆排序与后端适配**（storage，非作者 spec，依 D05）：M06–M10；SQL 权威正文和向量、Chroma 候选、历史可见版本、去重衰减、主动 CRUD 与配置召回数量已实现。`b0dab6a` 补齐 seed、原值原向量流式导出和批次原子导入、fork 隔离与关闭排空；独立审查发现的异步查询版本竞态、关闭与 update 竞态已回归。证据：`test_kernel_memory*.py`、`memory-transfer-independent-final.txt`，42 项作者及独立用例通过。完整 Memory 的真实 LLM/embedding 链继续由 V05 验收。
- [x] **D07 有界计算接入**（storage，非作者 threads，依适用封存接口）：`8194e42` 由 StageStore 懒创建共享原生压缩池，Writer.write_json_chunks 供 Thread/Memory/ResourceCalls 共用（`0ef012a` 已统一成熟原生 JSON push 接口）。默认 4 workers、512KiB 待处理原始块，另有固定前缀、结果及 zlib/SQLite 内存；256KiB 以下走同步短路径。按输入顺序回写，异常和主线程等待中断均排空已启动任务。145 项相关组合及 15 项最终独立/探针测试通过；本机产品 4×10MiB 三次中位 296ms，相对同步 912ms 缩短约 67.5%，同步事件循环空窗仍约 207ms。证据：[性能报告](../../research/core-next/persistence-performance-20261004.md)、`test_kernel_compression*.py`。服务器与整步成本继续归 X08/V04；事务外暂存保留为已测候选。

### 4.2 时间、领域与结果

- [x] **T01 step/phase 调度插件**（threads，依 K07/X06）：S01/S02/S06，代码步骤顺序、hooks、同阶段共享视图、前序修改可见、跨 moment scope 失效；直接 rule/behavior 与模型测量均走新接口。`63675b8` 的 CodeSchedule 沿唯一 Runtime 路径，Host 自动提供机制钩子和先排空后关资源的生命周期；实际 RuleDriver、LLM interview、ActorStore 与两个 Social 实例贯穿。作者及非作者相关 63 项通过，含二次取消导致收尾中断的独立红例修复，见 `schedule-results-independent-final-green.txt`。
- [x] **T02 动态激活与批处理**（threads，依 T01/D02）：A08/S03–S05，selector、并发优先级、同 actor 串行、信号合并、空槽立即补位、close 边界新任务、取消及未完成 activation；每主体错误范围明确。`f7b4a42` 的 collect 策略、稳定结果顺序、drain 消费及持久 ActorStore 按需加载已通过作者和独立集成；`23723ae` 增加索引流式选择、O(k) 驻留的抽样和完整结果行，实际 LLM interview 使用显式 tuple 字段路径；作者及独立复验通过。`35e7642` 的 Phase 容量覆盖 Runtime 默认值，并明确 serial 为 1；实际 ModelProvider 消费者验证 3 个并发 Driver 仍受独立的单请求服务许可约束。S03 与时长联组最终 195 项直接验收、38 项非作者复验通过；全运行共享物理请求许可继续归 K02。
- [x] **T03 plain 与外部机制扩展**（指定 owner，依 T01/K07）：B01/E01–E04，普通小状态、外部声明/schema/default、非 JSON 图/数值派生资源恢复；低依赖路径不加载无关重资源。证据：`88b8d3a`、`test_kernel_preparation*.py`、`test_kernel_graph_plugin.py` 和 `graph_environment.py`；18 项作者组合、15 项非作者复验覆盖异步外部准备、根事务、资源清理异常不能吞初始化失败、双取消时先等文件读取退出、准备巨值进入运行前释放、删除源后不重新读取而恢复图/数值派生资源。基础与 shell 独立安装另见 `packaging-independent-20261004.txt`。
- [x] **T04 round_robin 迁移**（spec，非作者 storage，依 T02/T03/D05）：B02，circle 配对、私信/广播、参与者、保留事实与当前收件范围、完整原文和恢复已实现，提交 `164fbe9`。2/4/6/20 人配对与旧算法对照；固定当前消息、历史从 100 增至 10000 时查询 VM 均为 111。独立发现的清空后重访旧轮次 total/items 不一致已修复，广播中途故障整批回滚、dirty 消息不进入恢复。证据：`test_plugin_round_robin.py`、`test_plugin_builtin_review.py`，15 项通过。交互游标版本调整仍须随 K04/K05 复验。
- [x] **T05 social 迁移**（指定 owner，依 T02/T03/D06）：B03–B05，拓扑与关系、post/comment/repost/like/follow/通知、推荐/热度/曝光/嵌入批处理；preview 无曝光，正文可完整取得，确定性逐事件和模型输入对照。`3f708cf` 已迁移独立 Social 实例、拓扑、关系、完整正文、通知、排名与恢复；28 项作者及独立相关用例通过，固定 5 个活动候选、历史 100→10000 的查询 VM 均为 480。`63675b8` 补齐实际 Runtime 自动 after_tick，两个独立实例的嵌入与曝光在 complete 前收束并可恢复；独立调度组复验通过，见 [Social 合同](social-contract.md)。最终真实模型与整套能力对照继续归 V03/V05。
- [x] **T06 结果与诊断插件**（指定 owner，依 T01/D04）：O01–O04，metrics/steps/events/资源/summary、大表外置及分区、逐主体/工具错误/时长/并发/termination；结束扫描与历史增长计数，失败资料保诊断身份。`63675b8` 已实现 StepResult 原文、流式表、有界范围读取、当前指标和累积短计数、逐激活结果及独立进度快照；非作者大正文单块读取、编码后字节预算、失败恢复和跨运行游标复验通过。`93c057b` 已实现规范物理模型/embedding 原文同事务累计投影，94 项作者组合与42项非作者复验覆盖重试、共享批次、缓存归属、未知token、取消/解码与HTTP完整视图；固定100→10000调用历史的查询VM均为292。`35e7642` 已接实际工具去重、终止原因、激活阶段、物理调用及步骤时长，195 项直接和 38 项独立复验通过；重试重复累计耗时的独立发现已修复。固定最近 5 条错误在 100/10000 条历史的查询 VM 为 184/174。`994cdc5` runner 已持久消费逐步时长并区分完整发布与退出清理失败；独立发现的误报 completed 已修复。`0ef012a` 的 TableValue/DatasetTable 明确完整 JSON 值、逐行表与封存引用，裸 Mapping/字符串被拒绝，避免静默保存键或列名；真实 DataFrame 显式 tight 转换保留列、索引与大整数。共享 dataset_plugin 统一 schema，双机制复用同一服务。分析目录在删除源后仍完整读取 Thread/Memory 原向量/结果/冷数据；O01/P06 独立消费者已通过，`4e10f34` 已将真实 Results、Thread 与资源统计接入离线工作台及 CLI；45 项直接、26 项非作者复验通过分页原值及解压计数。`1e300f9` 修复同名不同序号阶段的指标混合，11 项工作台直接和 30 项含真实结构的非作者组合通过。最终全量冻结继续由 V02/V08 验收。

### 4.3 数据、恢复与观察

- [x] **P01 按需权威状态**（storage，依 X03/X04/K03）：P01/P02，新薄状态接口、持久事实/当前投影/临时数据、局部写入与必要原子组、值类型/精度/插入顺序；热查询不重放历史，查询不构建全量 World。`492e9dd` 的 typed_records 实际插件及独立复验验证整数/文本键、大整数、浮点与列表顺序、追加唯一事实、执行权限、原子事实/投影回滚和完整恢复；2MB 冷事实下 100 次小投影更新的 Session 捕获小于 64KiB。`0ef012a` 的正式 Datasets、原生正文编码和 `8369abb` 的 SQL 正文引用共同完成按需读取消费者；不可变正文与热投影分开，派生运行资源由插件恢复。任意巨型轻字段仍有单值驻留下界，综合规模验收留 V04。
- [x] **P02 完整发布协议**（storage，依 P01/D04/D06/T01）：P03/P04，World/Actor/Thread/Memory 统一恢复身份，完整/诊断分离；多介质 prepare 与唯一完成权威按选定 SDD 实现，逐边界故障注入。检查点与归档间隔由存储合同规定。实现证据为 StageStore 唯一 complete 发布、Actor/Thread/Memory 同库规范写入、Workspace/Datasets 工件引用与 prepare→登记；`test_kernel_services.py`、`test_kernel_schedule.py`、`test_kernel_workspace_review.py`、`test_kernel_analysis_export.py` 验证规则与确定性模型贯穿、完整恢复及删源后独立分析。真实提供方故障链继续由 V05 验收，最终跨插件审查留 V07。
- [x] **P03 恢复、fork、export 与清理**（storage，依 P02）：P05/P06/P08，独立进程重新打开、来源身份、初始化差异、branch 隔离、选定历史完整点、来源丢失、可达清理；无旧 codec 兼容要求。`121923b` 已通过恢复包独立性、旧完整点、重复依赖仅复制一次、来源删除后再次恢复及离线清理的 52 项相关验收。独立审查发现的非根描述符缺失 changeset 导致误删问题已修复；全部身份与依赖验证先于删除。只读分析准备、统一跨插件恢复与大型占盘继续由 Q/P02/V04 验收，见 [生命周期合同](storage-lifecycle-design.md)。
- [x] **P04 大记录与持续成本**（storage，依 P01/P02）：P07，按块编码/记录定位/metadata 顺序访问、字节预算提前停止、范围下载不重解全前缀；宽 dict/单大 record、annotations/static metadata 下界单独报告。新增验收点：通用 SQLInformation 对查询所选巨字段的读取物化必须单列测量；HTTP 编码后拒绝超限响应仅控制传输字节。先验证现有 DocumentSpec/正文引用的正式机制，再确定 SQLite 原生长度预检等最小提供者改动，避免把 wire 预算当作底层内存上限。实际大根当前 root/current 合计约 1.417 GB，独立小记录压缩与两份正文构成主要空间代价；以真实样本比较成熟字典/聚合压缩及不可变正文布局，并验收同文件系统分叉共享与独立导出，尚未完成全根磁盘成本验收。`6a55d56` 已实现明确批次的不可变 SQLite 正文、规范热引用、同文件系统硬链接共享及单 current 只读准备；76 项相关回归与 9 项独立组合通过。真实 10000 条子集的 source/restore/view 三目录逻辑约 184.9MB，按 inode 去重分配约 61.8MB；同步 JSON/压缩耗时约 66.1 秒，`f4d03a6` 已采用 64KiB zstd 共享块，并只在单次读取作用域缓存最后一块；同 10000 条真实记录目录降至约 34.4MB，恢复约4.3ms、只读准备约3.5ms，逐值相等。`0ef012a` 已统一 python-rapidjson 原生 push；同 10000 条真实记录导入由 67.634s 降至 4.622s，目录大小相同，原文逐值相等。64KiB 输出块之外的输入 Unicode UTF8 缓存成本明确披露；31 文件 176 项、非作者 19 项和最终全部 kernel 516 项通过。`4ebc725` 已固定全部 1,448,471 条逐值比较、实际执行代码与 `0ef012a` 字节一致、226.54MB 总目录和独立冷进程 21.94MB 峰值；20 次热 header 更新的 Session 捕获恒为 2,416B，该 fixture 不等同完整社会步骤。`8369abb` 以正文引用、页字节预算及 expected_revision 修正 SQL 巨字段物化，20MB 正文查询的 SQLite 额外峰约170KB、Python峰约10KB；64 项直接、18 项非作者复验通过，NULL 原值、UTF8 全文与撤权限已覆盖。综合驱动、多核和向量索引驻留继续由 V04 验收。
- [x] **Q01 在线离线查询**（指定 owner，依 P02/K04）：Q01/Q02/Q04，status/版本水位、固定页/精确 total/typed cursor、索引追赶/准备/损坏重建、fork 源内容定位；查询进程与 producer 分离，冷追赶不能阻塞 status。证据：`17aeaa4`、[外部观察合同](observation-contract.md)、`test_kernel_observation*.py`。独立恢复进程准备固定完整视图，live status 持续可读；准备失败、清理、进程退出、稳定派生身份和真实 CLI 跨进程续读已验证。`6a55d56` 将只读准备收敛为一份 current 与完整点元数据，不生成新 root；不可变工件同文件系统共享。大规模空间与布局优化继续归 P04/V04。
- [x] **Q02 实时信息与 Thread tail**（指定 owner，依 D04/Q01）：Q03/Q06/N03，Thread 目录及可继续 tail、live 与完整 prefix、完成步 observed revision、慢消费者与 view 过期；固定分页读完后仍可消费新增而无无限订阅队列。证据：`17aeaa4`、`test_kernel_observation.py` 与独立 review；Thread 末尾游标保留追加位置，publish_step 索引确定已完整前缀，运行中未完成事实明确区分；固定 tail 的历史增长开销和独立 producer 故障已验证。
- [x] **Q03 服务及大表读取**（指定 owner，依 Q01/Q02/T06）：Q05/O03，Python/CLI/HTTP 同合同、bounded 接纳、慢 body/response、取消、分区 dataset refs 和巨正文；最终序列化大小符合预算。`17aeaa4` 已实现 Python/CLI/HTTP、慢请求有界槽、最终编码预算、结果及物理资源原文范围、完整视图生命周期；作者组合与非作者重审通过，见 `observation-validation-20261004.md`。`93c057b` 已接累计资源的HTTP live/complete消费；`35e7642` 已接 action_summary 与时长投影，非作者验证 prepared complete 排除 live 失败诊断；`0ef012a` 的 DatasetTable 已通过统一 Results/只读分析消费者；`8369abb` 完成 SQL 正文引用、Observation 查询字节预算与 read_document 版本参数，LLM/Shell/外部读取贯穿及独立复验通过。根审查随后确认 Results 页将任意业务值与 record_ref 放在同一层，存在引用形状碰撞；工作台逐行范围读取的规避方式又失去冷块分页复用。`4e10f34` 已统一内外 Results 的 value/payload_ref 显式页项外壳并恢复工作台分页消费；45 项直接、26 项非作者复验覆盖引用形状碰撞、NULL/false/大整数/Unicode、512B 页预算、恢复及跨页 ordinal。1000 条同块小记录只需一次文件打开、一次 BLOB 读取和一次实际解压。最终工作台、规模与主体效果仍归 U02/V04/V06。

> 最终独立审查发现的 K04、K05、P02 两项阻断已由 `2646feb` 修复：目录逐子路由执行发现权限，游标绑定授权范围与版本；行动处理器异常、取消及无效返回使共享步骤失效，Driver 捕获异常后仍无法发布。原独立失败用例保持不变，121 项作者相关测试及 9 项非作者边界测试通过；最终冻结全量继续归 V08。证据：`final-review-20261004.md`、`final-boundaries-independent-20261004.txt`。

## 五、P3 验收

验收从最小组合逐步到真实负载。每份报告说明源代码身份、数据来源和未覆盖风险，模型成功调用与仿真研究有效性分开判断。

- [x] **V01 能力矩阵闭环**（spec+各 owner，依 P2）：每个保留/重设/新增 ID 链接新版代码和验收，不保留空白完成项；审查旧测试删改理由及仍未迁移的 public consumer。最终逐项核对 78 个 ID，L09/L10 由 `89ad172` 补齐，R05 由 `bcff535` 恢复实际 Provider/Thread/错误与恢复回归。其余保留与重设能力均有消费者，未发现其他产品占位；见 `capability-review-20261004.md/json` 与能力矩阵。最新真实端点与自主信息获取效果继续由 V05/V06 独立验收。
- [x] **V02 确定性全量与工作台测试**（非作者，依 V01）：直接组后新入口全量、Node 工作台和明确的 performance tests；记录 skipped/deselected 与原因，所有红灯归因并处理。 `2590cf3` 精确快照 665 项确定性通过、15 项真实端点明确排除；实验组 59 通过，sqlite-vec 在隔离环境另跑 3 项通过。真实 Core pilot payload 的 Node 9 项全部通过；`5546a25` 将默认 Node 测试固定串行并更新锁依赖、原生工具链和完整 CI 入口，非作者重跑公开 CLI→Node 9 项通过。最终修改后的全量与远端 CI 继续归 V08/U04。
- [x] **V03 同语义旧新对照**（指定 owner，依 T04/T05/P03）：固定输入及模型假响应，对比初始/连续运行/恢复全值、逐笔顺序、完整 Thread、最终模型上下文、记忆正文及行动；纯环境与含 Driver 路径均测。 `bd37698` 以旧 96b1f3b 实际 World/行动循环/Memory/检查点和新版正式 Core 执行 12 个独立进程，八个最终分支的三步业务全值、顺序、实际决策材料及记忆原文/向量相等；各版本连续/恢复的完整 Thread 和实际模型请求逐值相等。独立审查发现并修复比较器只查片段的缺口，8 个原始损坏反例保持不变，作者与非作者 13 项通过。原文和来源身份见 `parity-20261004/`、`parity-report-20261004.md`。
- [x] **V04 长历史与大对象负载**（storage+query owner，依 P04/Q03）：复用实际大根或有明确证据的同规模数据，固定活动/扩大历史、固定历史/扩大活动、单大 entry；计 CPU/墙钟/RSS/读写/扫描量、cold/warm/index/WAL 峰值与多步斜率。大工件远端或实验完清理，保小证据。 `380a7a1`、`6d3100c` 和 `5546a25` 收拢完整真实大根、多步历史/活动量、巨大单值、完整认知与多核计量，以及当前编码下独立观察测量；两轮非作者审查支持已测 Core 机制。详见 `performance-acceptance-20261004.md`、`final-realtime-ci-storage-review-20261004.md`。保留 Chroma 有效向量驻留、changeset 长链、跨文件系统复制和真实产业机制迁移的范围，未宣称完整社会长期规模保证。
- [ ] **V05 真实 LLM/embedding 端到端**（指定 owner，依 D06/T05/P03）：重新核实受支持 profile；覆盖旧 13 项真实能力及独立进程退出恢复链；完整 tools/记忆/实际预算不弱化，不把缺配置 skip 当成功。保留失败证据及限定调用规模。
- [ ] **V06 主体效果对照**（指定 owner，依 K06/D05/V05）：动态 action 与 VFS 路径相对旧输入能力，核对信息找到/完整获取、行动完成、判断结果及成本；工具数量/返回长度下降不单独构成优化。
- [x] **V07 跨插件故障与独立审查**（非作者，依 P2）：partial write 后工具异常不能吞失效状态、cancel 后无后台写、Thread/Memory 失败窗口、同步/异步插件清理、用户信息范围、索引/recovery 一致性；本轮阻断问题已修复并复验。最终冻结产品由未参与产品修改的 GPT‑6.1 Sol 独立审查，核对 Runtime/Actions 共享失效、Thread 原子回滚、Memory 失败恢复、重复取消、HTTP 排空与授权范围；相关消费者纳入两平台各 838 项最终通过结果。见[本轮能力复审](../../research/core-next/acceptance-20261004/capability-review.md)与[测试迁移复审](../../research/core-next/acceptance-20261004/migration.md)。真实服务、主体效果及完整业务长期负载仍分别保留后续验收边界。
- [ ] **V08 最终冻结全量**（指定 owner，依 V02–V07）：最后产品修改后重跑受影响组及全量，真实部署与提交源码按字节比较绑定；不以旧源码结果验收新存储或恢复机制。

## 六、交付

公开入口、研究者工作流与代码使用同一套合同。提交、推送、合入和部署分别记录，依主智能体对当前授权的判断执行。

- [ ] **U01 新版示例与完整 pilot**（指定 owner，依 K07/D06/T06）：迁移 U01/U02，从零安装、初始化、两轮信息—行动—记忆—测量；小实验仍保存完整关键输出，规则用户不用模型即可运行。`994cdc5` 已提供轻公共入口、正式 Thread/Memory 工厂、run_plan、规则例与双机制两轮 pilot；runner/services/公开入口相关独立复验 13 项通过，双机制 pilot 使用确定性提供方。`0ef012a` 已补逐激活记忆策略及默认 step 时间序号；最终 skill 资产和真实提供方链仍待完整验收。
- [x] **U02 工作台与观察分析**（指定 owner，依 Q03/U01）：U03/U04，新产物支持 run/moment/actor 精确范围、真实结果、配置差异新版本、虚拟列表与单文件导出；无数据明确空态。`4e10f34` 的 RunSelection/CLI 选择完整点，真实 pilot 表格与趋势经现有 React 渲染器消费；完整 Thread 超过1000条仍全部导出，源/步/Thread/seq可追溯，失败清理后可重试。`1e300f9` 固定重复阶段的序号身份；11 项直接和30项非作者组合通过，既有Node8项已通过，最终Node全量归V02/V08。离线导出会物化用户明确选择的全量内容，巨型HTML的内存成本仍随所选数据增长。
- [ ] **U03 开发文档与清理**（指定 owner，依 V01/U01/U02）：插件分层/依赖例、View/Action/Driver/Schedule、状态/恢复/查询教程；清理旧公开单体入口及无消费者占位逻辑，无兼容桥和重复权威文档。
- [ ] **U04 发行与提交交付**（主智能体/指定 owner，依 V08/U03）：版本/锁/README/发行说明一致，能力证据报告收拢，确认无密钥/巨量产物；提交与推送核对 exact HEAD，按本轮授权处理合并和工作树，区分实际部署状态。

本次工作的结束条件是新版能力矩阵和完整验收闭环。若某个新增插件接口未通过贯穿情景，应回到该接口的最小合同调整；已通过的小实验继续作为证据保存，待办保持真实状态。
