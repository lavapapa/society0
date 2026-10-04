# 最终交付核对

本次按 PRD、SDD、agent-filesystem-design 和能力对照核对当前产品入口、实际消费者与验收索引。审查以本轮工作树为对象，真实端点、发行安装和最后全量的结果由对应集成工序留证。以下记录将静态可达性、确定性运行证据和成本边界分别表述。

## 一、接线

capability-parity 的独立能力编号为 78 个；acceptance-map 中显式编号与编号区间展开后覆盖全部 78 个。对 capability-parity 的本仓相对文件链接及 `文件::函数` 符号逐项检查，文件和函数均存在。这个检查证明验收索引可达；行为证据由各条现有用例及本轮新增负例承担。新增 Driver、Schedule、文件系统与认知扩展使用本轮独立测试，不引用旧历史窗口或旧工具布局充当新合同。

Driver 工厂由普通 Plugin 服务提供，actor_plugin 消费服务引用，主体记录保存具名 driver 和 config，恢复安装时拒绝未解析的 driver 名。rule_run、codex_subscription 和正式 skill starter 都有实际工厂消费者。RuleDriver、LLMDriver 与测试中的第三 Driver 进入同一 ActivationContext / AsyncExitStack 合同；扩展先建立作用域，input_builder 建立实际输入后运行准备回调。context.inputs 借用当前输入，context.messages 保存新增认知材料；文件视图可读取必要上下文，完整 Thread 由同一模型请求继续传递。

context.mounts 按普通名称路由第三插件的 list/stat/read/revision 视图，MemoryFiles 为其中一个提供者，ActorFiles 未读取 MemorySQL 私有 schema。记忆写入、召回、主动工具八组合仍独立；关闭主动工具隐藏 records 挂载，召回材料继续进入当前认知输入。规则结构化经历和完整 Thread 提取分别有消费者，失败与截断不进入成功经验写入。

Schedule.next_step(completed_step) 向 runner 提供 StepPlan，Runtime 执行与完整步骤发布保持独立。SequenceSchedule 使用完整时间线定位完成步骤后的计划，恢复例子与测试沿此合同执行。/results 按主体所属 Thread 的工件引用读取原文，当前激活、跨激活及完整点恢复均有实际用例。

## 二、文档

README 与 skill 使用公开工厂、RunPlan 和共享服务；llm-contract 文件参数与 _tools 声明相符，read/ls/find/grep 与 shell data query 分工有真实消费者。正式文档未找到已删除的 data_list/data_query/data_read 元工具、LLMDriver(memory=...) 或旧 ExposureMemory/StudyDriver 包装。runtime-contract 的 RuleDriver 是该片段自定义的第三驱动，RuleDriver() 在该片段有效。results-contract 保留显式低层 actor 工厂示例，公开正式研究入口使用服务引用。

implementation-result 已链接本轮实施记录，并明确已持久化正文的范围读取与 Overlay 巨文件首次物化成本；release-6.0.0 的独立 Schedule、统一文件入口、llm/shell 引入原生适配器与基础规则安装边界均与当前源声明一致。

社交插件的帖子详情与 profile 通过 Information 原文路径完整读取，comments、likes、profile、正文及依赖版本保持原合同；推荐、曝光和消费通知仍由真实产生业务效果的动作承担。直接读取帖子正文未增加浏览计数，撤权和完整点恢复测试覆盖信息路径。

核对发现 F06 schema 的必需信息缺口：原 metadata 没有类型、业务含义、时间说明和可执行查询例。先让空数据集 schema 文件用例因 field_metadata KeyError 失败，随后在 DatasetSpec 增加可选 field_descriptions、time_description、query_examples，并复用既有 PRAGMA 字段类型。metadata 保留 fields，新增 field_metadata、time_description、query_examples、ref、cost。默认查询例可直接用于 Query，空集合也执行成功；社交 posts 填写实际字段和 tick 口径。自定义说明/例子的注册测试同样执行真实查询。information-sql-contract 已同步这些真实参数并修正重复章节编号。

## 三、验证

schema 补全的 cross-review、social discovery、SQL 与 SQL review 相关用例通过；最后一次包含安装完成后的原生用例为 33 项。原生批处理 wheel 安装后重新执行 cross-review、native stream、ActorFiles 与 social discovery，最终 35 项通过。额外非作者用例用 6001 行 Unicode 原文、4093 字节块和无换行末行，对照全部原始命中、行号和字节偏移，并核对批次确实减少回调次数。取消用例同时覆盖等待 read 和等待 sink 时的收束，结果工件范围、失败诊断、恢复归属及全部选中挂载冻结由现有 ActorFiles / cross-review 用例覆盖。

原生批处理安装期间出现过新 Python sink_batch 与旧 wheel 的短暂签名不匹配，测试等待 entered 屏障因此挂起。终止的是本次本人 pytest 进程，取消测试现已同时等待 worker 与 entered，提前失败会直接显露；安装后全组通过。未将该过程视为产品成功证据。

SQL schema 元数据成本随注册字段数变化，不扫描业务记录。授权过滤计数和排序成本由 SQLite 计划决定，抽样扫描授权键；Dataset 合成 JSON 首次生成完整单条记录并保留一个临时槽，DocumentSpec 使用真实增量范围。原生搜索保存全部命中，行缓冲成本受最长行决定。独立记忆提取在原模型许可授予后加载完整历史；SDK 活跃期间持有同一历史，物理重试与纠正轮借用它。最终打包后的全量、真实端点和资源对照由主控及集成审查记录收口。

当前核对未发现新增接口缺少正式消费者的交付阻断。已发现的 schema 接线缺口完成补全并进入回归。上述静态索引检查与焦点验证共同支持此次合同一致性判断，发行和真实运行结论继续以最终集成证据为准。
