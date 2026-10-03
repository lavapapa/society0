# Core 实施验收映射

本文把能力对照中的全部 ID 分配到实现边界和验收文件。文件名为预定责任位置，可以随实现调整；只有对应 TODO 与测试证据同时完成才能认定交付。旧 API、旧文件格式和旧单体 World 不进入新合同。

## 一、共同接口

共同接口覆盖插件作者必需的概念，并用实际领域消费者约束扩展成本。不同插件仍组成一个共享环境。

| 能力 ID | 责任模块 | 验收位置与关键负例 |
|---|---|---|
| E01、E02、N05 | kernel/plugins 与机制插件 | test_kernel_plugins、test_kernel_plugins_review；缺依赖、环、重复服务、半初始化与逆序清理 |
| E03、S01、S02、S06 | kernel/runtime 与 schedule | test_kernel_runtime、test_kernel_runtime_review、后续 test_kernel_schedule；时点、prepare 共用、关闭、无模型规则运行 |
| N01、N02、L01、L02 | kernel/interaction 与 llm ledger | test_kernel_interaction、test_kernel_interaction_review、test_kernel_llm；动态目标、发现后失效、schema、实际行动预算 |
| N03、N04、B05 | kernel/information_sql 与 shell | test_kernel_information_sql、test_kernel_information_sql_review、test_kernel_shell；行范围、全文、深页索引、抽样成本、UTF8 与原文范围 |
| K 层贯穿消费者 | examples/core_next/shared_environment | 双机制事务、两个主体、真实 shell 查询与行动、完整发布及新目录恢复 |

## 二、主体与机制

主体身份、认知过程和领域规则分别拥有权威数据，公共服务通过依赖提供。规则与 LLM 使用同一信息和行动合同。

| 能力 ID | 责任模块 | 验收位置与关键负例 |
|---|---|---|
| A01、A02、A08 | kernel/actors、runtime | 预定 test_kernel_actors；持久身份、persona、状态引用、选择顺序、抽样、规则驱动及批量结果 |
| A03–A07、L03–L12 | kernel/llm、models、认知输入插件 | test_kernel_llm 与独立 review；完整消息、required、terminal、重复调用、失败预算、length、访谈与结构化结果 |
| M01、M02 | kernel/threads 与 llm | test_kernel_threads、test_kernel_threads_review、test_kernel_llm；消息水位、巨正文范围、同 moment 跨进程续激活、完整恢复 |
| M03–M10 | kernel/memory 与可选检索适配 | 预定 test_kernel_memory 及 review；三个开关八组合、pending/receipt、逐条向量、失败重试、排序衰减、分叉隔离与导入导出 |
| R01–R05 | kernel/models 与可复用 provider 管理器 | test_kernel_llm 假 SDK、预定 test_kernel_models；物理 retry、timeout/cancel、session、strict/tool_choice、embedding 合批、参数与凭据边界 |
| S03–S05 | kernel/runtime、schedule 与 ActivationPool | test_activation_pool、test_kernel_runtime、预定 test_kernel_schedule；逐主体失败策略、立即补位、同主体串行、close 新任务和取消 |
| B01、E04 | plain 与自定义机制插件 | 预定 test_kernel_plain；小状态、派生图/数值结构、恢复、低依赖路径 |
| B02 | round_robin 插件 | 预定 test_kernel_round_robin；配对、轮次、私信与广播、参与者、保留消息、主体所见内容和恢复 |
| B03、B04 | social 插件 | 预定 test_kernel_social；逐条行动、拓扑、完整活动池推荐、曝光与 preview、批量 embedding、状态及输入对照 |

## 三、运行产物

权威状态、完成身份与观察水位同时进入验收。查询端看到运行中进展时必须能够区分完整恢复范围。

| 能力 ID | 责任模块 | 验收位置与关键负例 |
|---|---|---|
| P01、P02、P07 | kernel/storage、组件 schema 与信息提供者 | test_kernel_storage、review、threads、information_sql；类型与顺序、原子组、借用失效、分块、宽行下界与持续成本 |
| P03、P04、P08 | kernel/storage、runtime、threads、memory | 存储与运行故障测试；混合产物先准备、唯一完成标记、丢回执、dirty current、业务与诊断区分 |
| P05、P06 | storage 生命周期与导出接口 | 预定 test_kernel_storage_lifecycle；fork、初始化差异、独立恢复包、清理可达性、来源消失、未完成数据排除 |
| O01–O04 | results、diagnostics 插件 | 预定 test_kernel_results；metrics、表、生成器、逐主体与物理调用计数、诊断写失败、大行与范围 |
| Q01、Q02、Q04 | observation 只读服务 | 预定 test_kernel_observation；在线/离线、独立进程、status、live/complete、固定版本、派生索引重建 |
| Q03、Q05、Q06 | Thread tail、Python/CLI/HTTP 适配 | 预定 test_kernel_observation_http；目录/末页后新增、字节预算、慢请求/响应、饱和和取消 |
| U01、U02、U05 | 公共入口、skill、完整 pilot、README | 迁移 test_skill_starter 与独立新入口 e2e；新安装、规则小例、两轮记忆和访谈、冻结配置及实际产物 |
| U03、U04 | 工作台数据适配与渲染器 | test_workbench_renderer 与 Node model/render tests；真实数据、精确主体与时点、空态、虚拟列表、配置新版本 |

## 四、跨层验收

确定性对照使用同一输入与假模型响应核对动作、逐笔顺序、全值恢复、完整 Thread 和实际模型输入。真实服务验收另行记录模型及端点合同，覆盖行动、记忆、embedding 和独立进程恢复。性能负载分别扩大累计历史、活动量和单大对象；报告墙钟、CPU、进程组 RSS、持久化总空间、在途字节与扫描量，比较纯机制步骤和含 Driver 步骤。

先在小样例重现失效，再建立最小实现和直接测试。独立审查发现必须进入回归测试；最终全量基于最后产品源码执行。阶段报告继续保留未覆盖项，局部测试数量与整体完成状态分别记录。
