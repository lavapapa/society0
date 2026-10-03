# 新版 Core 实施待办

本清单管理本次从能力盘点到完整交付的工作。产品合同见 [PRD](PRD.md)，旧版有效能力与证据见 [能力对照](capability-parity.md)。任务通过“先失败规格、最小实现、直接验收、独立审查”推进；实验成功与产品完成分别勾选。每个完成项需记录代码、测试或报告位置，未实现项保持未勾选。

## 一、约定

任务 ID 在实施中保持稳定，负责人可以调整。依赖表示开始或验收所需的输入，允许不争用同一文件且无语义依赖的任务并行。阶段内某个原型完成不代表整阶段完成。

当前工作树为 `codex/society0-core-next`，合并基线 `738ba70cf7881649c38422d4b3f00ffed12ab51c`。能力盘点由 spec 负责；产品主机与调度由 threads 按主智能体分工推进，存储/数据实验由 storage 负责，主智能体统筹 PRD、SDD 与整体交付。真实验收按当前用户授权及具体运行合同执行；密钥仅通过进程环境使用。I03 的确定性基线与 V05 的真实服务验收分别记录。

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
- [ ] **X04 大集合与热工作集原型**（storage，依 X03 初步）：固定活动量扩大历史，测定位、分页、range、typed 值/键、顺序、根载入与 schema 验证；小 doc 与大 dataset 分开；比较扫描/物化字节和 RSS，检查 SQL N+1 及 `.items()` 全量物化。
- [ ] **X05 VFS 与自主分析原型**（threads 试验，spec 产品）：Bashkit 五项选型试验和 shell 产品读写/管道/完整文本输出/原文字节通路已通过；见 [试验](shell-experiments.md)、[合同](shell-contract.md)。懒文件整读与原生非法 UTF8 替换边界已明确；SQL 数据集的 tail/sample 与完整模型任务对照待接入。
- [x] **X06 Driver/时间原型**（threads，独立审查 spec）：规则与假驱动、顺序阶段及显式独立并发、同 moment 游标、信号合并、waiting/incomplete、整步预算已验证。非作者加入真实 PluginHost+StageStore 双机制与恢复，作者/非作者共 21 项通过，见 `runtime-review-green.txt`；LLM 实际循环继续归 D02。
- [x] **X07 OpenViking 复用判断**（指定 owner，无前置）：依主智能体研究核对 URI/目录/原文/摘要/检索资产、依赖成本与恢复边界；输出直接依赖、适配或借鉴的证据。不得用摘要默认裁剪 Thread。证据：[官方源码研究](openviking-study.md)，已核对版本/读取成本/快照/权限/依赖并建议 URI/View 借鉴与可选 SDK；直接集成与性能原型尚未实施。
- [ ] **X08 有界多核小实验**（storage，依有明确块输入）：压缩线程、纯 Python 计算进程与串行，对相同完整数据逐值核对，计墙钟/CPU/在途字节/进程 RSS/IPC；与产品并行接入任务 D07 分开。
- [ ] **X09 P0 独立审查与取舍**（非作者，依 X01–X08）：指出原型未覆盖的故障与语义，确定首版实现方案并更新 SDD；仅选择已有消费者支持的公共接口，保留失败实验数字。
- [x] **X10 实现语言选择**（主智能体，用户新增要求）：Python 公共 Core/插件接口，成熟 C/Rust 组件处理数据与重计算，TypeScript 用于工作台；新增 Rust 扩展由实测热点触发。证据：[语言决策](language-decision.md)、原生压缩与 SQLite Session 试验。整步性能验收继续归 V04。

## 三、P1 主机

这一阶段形成可由用户调用的最小产品，承接 P0 结论。一个共享环境中的两个机制插件是最低集成消费者，普通单机制和规则主体仍有短路径。

- [x] **K01 Core 插件主机 TDD**（threads，依 X01/I04）：正式主机与 runtime_plugin 通过真实持久化双机制消费者集成；依赖、实例、冲突、环、清理及无全局 registry 已验。证据：`test_kernel_plugins*.py` 和 `test_kernel_runtime_review.py`，入口使用独立 Core 服务，无旧 World 依赖。Actor/LLM 全能力继续归后续任务。
- [ ] **K02 生命周期与运行 scope**（threads，依 K01）：按依赖启动、反向关闭、部分初始化失败、取消、总资源预算、task-local actor 绑定与失效；普通异常清理和业务回滚分别验收。由 spec 独立审查 X01 与产品差异。局部主机生命周期审查已通过；actor 绑定、运行总资源与消费者接入未完成。
- [x] **K03 Actor/Object/ResourceRef**（spec，依 K01）：身份/类型/实例、主体主观状态与领域角色事实、对象引用按需解析；保持值精度/类型/顺序，禁止复制整个环境到每主体 runtime。证据：compose 的 Plugin schema/initialize（`ce83ef2`）与 ActorStore/Runtime 懒解析（`f7b4a42`）；`test_kernel_actors*.py`、`runtime-actor-independent-green.txt`。冷热分表、一级主观状态局部更新、角色索引和精确计数已验证；全量资料读取成本仍随被激活主体自身资料大小增长。
- [x] **K04 View 与 Access**（spec，非作者 storage/threads，依 X02）：Information 路由与 SQLInformation 提供来源/时点/版本、主体绑定、授权数量、固定版本游标、原生 BLOB 范围和有索引分页。证据：`test_kernel_interaction*.py`、`test_kernel_information_sql*.py`、[SQL 合同](information-sql-contract.md)。四项 SQL 独立发现已进入回归；通用外部服务总字节预算继续归 Q03。
- [x] **K05 动态 Action 与 Intent**（spec，非作者 threads，依 X02）：按目标类型注册模板，find/describe/invoke、schema、执行重验与结构化 outcome 已实现。证据：`test_kernel_interaction*.py`、`test_kernel_shell*.py` 与 [交互合同](interaction-contract.md)；LLM 内预算/terminal/required 组合继续归 D02/D03。
- [ ] **K06 workspace 与 LLM 交互适配**（指定 owner，依 K04/K05/X05）：私有资料读写查删、lazy VFS、完整原文/data query/分页/总数/tail；写世界经过 action，规则驱动直接用结构化服务。`f7b4a42` 已通过真实 Bashkit+LLMDriver+ActorStore 的跨 Moment 完整恢复及失败回归；完整工作区重复快照成本和真实模型自主分析继续验收。
- [x] **K07 规则 Driver 与贯穿样例**（spec，依 X06/K04/K05）：`examples/core_next/shared_environment.py` 使用 PluginHost、StageStore、Runtime 与两个规则主体，实际完成 SQL 查询、抽样、jq 分析、动态行动和消息读取；封存输出与 workspace 后恢复逐值相等。证据：`test_kernel_information_sql.py` 中完整例及 `information-sql-green.txt`。跨 LLM 激活的 workspace/回执消费继续归 K06/D04。
- [ ] **K08 P1 独立审查**（非作者，依 K01–K07）：角色伪造/范围泄漏的语义负例、失效对象、执行时条件变化、partial write 与资源累计、懒加载实际分配；按项目约束做能力正确性审查，不开展额外安全性研究。

## 四、P2 对标

这一阶段迁移全部有效运行能力。每组标注能力 ID，完整旧版行为逐项获得新入口证据；若接口改变，使用新接口表达等价任务。

### 4.1 主体与模型

- [ ] **D01 Provider 插件**（threads，依 K02）：R01–R05，多模型/端点/并发/timeout/取消/retry/trust_env/session、strict/parallel/tool_choice、embedding 维度/微合批。ModelProvider 薄适配及固定消息水位已实现；完整配置、embedding 合批留证与真实端点验收继续推进。
- [ ] **D02 LLM Driver 基础循环**（threads，非作者 spec，依 K05/D01）：动态元工具、实际行动账本、结构化测量、terminal/required、完整 trace 已提交 `df3e805`，见 [模型合同](llm-contract.md)。默认 persona/View/记忆认知构造与模型选择继续接入，全部完成后验收 A01–A07、L01–L04/L11/L12。
- [x] **D03 LLM 预算与失败组合**（threads，非作者 spec，依循环实现）：L05–L10 的总/逐 action/turn 预算、失败尝试、重复 call、parallel false、length/empty/schema/provider 错误已通过作者及独立测试。三个独立发现（物理请求水位漂移、诊断重复复制历史、恢复终止回执后多请求）已回归；见 `test_kernel_llm*.py`、`test_kernel_models_review.py` 和 `llm-manager-regression-green.txt`。真实服务与记忆组合留在 V05/D05。
- [x] **D04 完整 Thread 与续激活**（storage/threads，非作者 spec，依 D02/K02）：ThreadStore 的分块原文、请求水位、tail/range 和完整恢复已通过作者与独立测试，见 [Thread 合同](thread-contract.md)。持久工具回执、跨进程同 moment 定位和 shell 旧产物消费已集成；`f7b4a42` 增加工作区恢复和固定记忆输入水位，`f273b15` 已将认知输入与消费游标原子发布；`cf08abc` 完成 CognitiveInput、变化背景追加、A→B→A 和独立进程恢复。证据：`test_kernel_threads*.py`、`test_kernel_cognition*.py`、`cognition-memory-integration-green.txt`，相关 99 项通过。长 Thread 不裁剪；真实提供方端到端继续归 V05。
- [x] **D05 记忆插件与三个开关**（storage，依 D04/D01）：M03–M09；逐一验证召回/提取写入/主动记忆工具 2³ 组合、interview 默认测量、budget/length 无成功记忆、提取失败/空选择/同 Thread 重试/single-flight/pending→write→receipt。同一有效作业重试避免重复提取；完整步骤之后、崩溃前发生的外部调用可能重算，不从 dirty 诊断偷偷恢复。证据：`cf08abc`、`test_kernel_memory*.py`、`test_kernel_cognition.py` 默认真实组件组合；31 项 Memory 作者/独立用例及 99 项相关组合通过，含访谈默认不提取、长度失败不生成记忆、提取纠正和回执恢复。资源装配、传输及真实服务继续由 D01/D06/V05 验收。
- [ ] **D06 记忆排序与真实后端适配**（storage，依 D05）：M06–M10；逐条向量/正文/时间/重要性、去重衰减/top_k、branch/active epoch/update/delete/export/import、读前等待必要写入；SQL 权威正文与向量、Chroma 派生候选，检索替换对比候选及最终主体上下文。当前核心及历史版本作者/非作者 25 项通过，见 `memory-independent-version-green.txt`；嵌入等待期间的版本变化曾导致漏旧版本/混未来正文，两项独立失败已修复。`cf08abc` 已补齐提取协议、主动四工具与配置召回数量；导入导出、seed/资源生命周期及真实提供方链仍未完成。
- [ ] **D07 有界计算接入**（指定 owner，依 X08/适用封存接口）：产品使用有界线程压缩或已证明隔离的计算，维持顺序和总预算；多核完成顺序不决定业务优先权，单块过大显式处理。

### 4.2 时间、领域与结果

- [ ] **T01 step/phase 调度插件**（threads，依 K07/X06）：S01/S02/S06，代码步骤顺序、hooks、同阶段共享视图、前序修改可见、跨 moment scope 失效；直接 rule/behavior 与模型测量均走新接口。
- [ ] **T02 动态激活与批处理**（threads，依 T01/D02）：A08/S03–S05，selector、并发优先级、同 actor 串行、信号合并、空槽立即补位、close 边界新任务、取消及未完成 activation；每主体错误范围明确。`f7b4a42` 的 collect 策略、稳定结果顺序、drain 消费及持久 ActorStore 按需加载已通过作者和独立集成；完整 selector 与认知组合继续验收。
- [ ] **T03 plain 与外部机制扩展**（指定 owner，依 T01/K07）：B01/E01–E04，普通小状态、外部声明/schema/default、非 JSON 图/数值派生资源恢复；低依赖路径不加载无关重资源。
- [ ] **T04 round_robin 迁移**（指定 owner，依 T02/T03/D05）：B02，逐轮配对/私信/广播/参与者/保留消息/FoV/恢复；配置策略名逐项核实，未实现的旧枚举不冒称迁移完成。
- [ ] **T05 social 迁移**（指定 owner，依 T02/T03/D06）：B03–B05，拓扑与关系、post/comment/repost/like/follow/通知、推荐/热度/曝光/嵌入批处理；preview 无曝光，正文可完整取得，确定性逐事件和模型输入对照。
- [ ] **T06 结果与诊断插件**（指定 owner，依 T01/D04）：O01–O04，metrics/steps/events/资源/summary、大表外置及分区、逐主体/工具错误/时长/并发/termination；结束扫描与历史增长计数，失败资料保诊断身份。

### 4.3 数据、恢复与观察

- [ ] **P01 按需权威状态**（storage，依 X03/X04/K03）：P01/P02，新薄状态接口、持久事实/当前投影/临时数据、局部写入与必要原子组、值类型/精度/插入顺序；热查询不重放历史，查询不构建全量 World。
- [ ] **P02 完整发布协议**（storage，依 P01/D04/D06/T01）：P03/P04，World/Actor/Thread/Memory 统一恢复身份，完整/诊断分离；多介质 prepare 与唯一完成权威按选定 SDD 实现，逐边界故障注入。checkpoint 与归档间隔若改变写入产品合同。
- [ ] **P03 恢复、fork、export 与清理**（storage，依 P02）：P05/P06/P08，独立进程重新打开、来源身份、初始化差异、branch 隔离、实际旧版本、analysis/restore scope、来源丢失、可达清理；无旧 codec 兼容要求。
- [ ] **P04 大记录与持续成本**（storage，依 P01/P02）：P07，按块编码/记录定位/metadata 顺序访问、字节预算提前停止、范围下载不重解全前缀；宽 dict/单大 record、annotations/static metadata 下界单独报告。
- [ ] **Q01 在线离线查询**（指定 owner，依 P02/K04）：Q01/Q02/Q04，status/版本水位、固定页/精确 total/typed cursor、索引追赶/准备/损坏重建、fork 源内容定位；查询进程与 producer 分离，冷追赶不能阻塞 status。
- [ ] **Q02 实时信息与 Thread tail**（指定 owner，依 D04/Q01）：Q03/Q06/N03，Thread 目录及可继续 tail、live 与完整 prefix、完成步 observed revision、慢消费者与 view 过期；固定分页读完后仍可消费新增而无无限订阅队列。
- [ ] **Q03 服务及大表读取**（指定 owner，依 Q01/Q02/T06）：Q05/O03，Python/CLI/HTTP 同合同、bounded 接纳、慢 body/response、取消、分区 dataset refs 和巨正文；最终序列化大小符合预算。

## 五、P3 验收

验收从最小组合逐步到真实负载。每份报告说明源代码身份、数据来源和未覆盖风险，模型成功调用与仿真研究有效性分开判断。

- [ ] **V01 能力矩阵闭环**（spec+各 owner，依 P2）：每个保留/重设/新增 ID 链接新版代码和验收，不保留空白完成项；审查旧测试删改理由及仍未迁移的 public consumer。
- [ ] **V02 确定性全量与工作台测试**（非作者，依 V01）：直接组后新入口全量、Node 工作台和明确的 performance tests；记录 skipped/deselected 与原因，所有红灯归因并处理。
- [ ] **V03 同语义旧新对照**（指定 owner，依 T04/T05/P03）：固定输入及模型假响应，对比初始/连续运行/恢复全值、逐笔顺序、完整 Thread、最终模型上下文、记忆正文及行动；纯环境与含 Driver 路径均测。
- [ ] **V04 长历史与大对象负载**（storage+query owner，依 P04/Q03）：复用实际大根或有明确证据的同规模数据，固定活动/扩大历史、固定历史/扩大活动、单大 entry；计 CPU/墙钟/RSS/读写/扫描量、cold/warm/index/WAL 峰值与多步斜率。大工件远端或实验完清理，保小证据。
- [ ] **V05 真实 LLM/embedding 端到端**（指定 owner，依 D06/T05/P03）：重新核实受支持 profile；覆盖旧 13 项真实能力及独立进程退出恢复链；完整 tools/记忆/实际预算不弱化，不把缺配置 skip 当成功。保留失败证据及限定调用规模。
- [ ] **V06 主体效果对照**（指定 owner，依 K06/D05/V05）：动态 action 与 VFS 路径相对旧输入能力，核对信息找到/完整获取、行动完成、判断结果及成本；工具数量/返回长度下降不单独构成优化。
- [ ] **V07 跨插件故障与独立审查**（非作者，依 P2）：partial write 后工具异常不能吞失效状态、cancel 后无后台写、Thread/Memory 失败窗口、同步/异步插件清理、用户信息范围、索引/recovery 一致性；所有阻断问题修复并复验。
- [ ] **V08 最终冻结全量**（指定 owner，依 V02–V07）：最后产品修改后重跑受影响组及全量，真实部署与提交源码按字节比较绑定；不以旧源码结果验收新存储或恢复机制。

## 六、交付

公开入口、研究者工作流与代码使用同一套合同。提交、推送、合入和部署分别记录，依主智能体对当前授权的判断执行。

- [ ] **U01 新版示例与完整 pilot**（指定 owner，依 K07/D06/T06）：迁移 U01/U02，从零安装、初始化、两轮信息—行动—记忆—测量；小实验仍保存完整关键输出，规则用户不用模型即可运行。
- [ ] **U02 工作台与观察分析**（指定 owner，依 Q03/U01）：U03/U04，新产物支持 run/moment/actor 精确范围、真实结果、配置差异新版本、虚拟列表与单文件导出；无数据明确空态。
- [ ] **U03 开发文档与清理**（指定 owner，依 V01/U01/U02）：插件分层/依赖例、View/Action/Driver/Schedule、状态/恢复/查询教程；清理旧公开单体入口及无消费者占位逻辑，无兼容桥和重复权威文档。
- [ ] **U04 发行与提交交付**（主智能体/指定 owner，依 V08/U03）：版本/锁/README/发行说明一致，能力证据报告收拢，确认无密钥/巨量产物；提交与推送核对 exact HEAD，按本轮授权处理合并和工作树，区分实际部署状态。

本次工作的结束条件是新版能力矩阵和完整验收闭环。若某个新增插件接口未通过贯穿情景，应回到该接口的最小合同调整；已通过的小实验继续作为证据保存，待办保持真实状态。
