# 主体驱动与认知扩展实施证据

本次实现对应文件交互规格第三部分。权威主体目录继续保存 driver 名称、config 与主观状态，运行资源经已有 Plugin 服务引用安装，主体实际激活时取得驱动。规则运行的基础路径保持可选模型、Thread 和记忆依赖按消费者导入。

## 一、装配

新增 `rule_driver_plugin`、`llm_driver_plugin` 各提供 factory 服务；`actor_plugin` 支持命名 factory 服务引用并自动声明依赖，安装与恢复时核对现存主体全部 driver 名称可解析。原 callable 与安装期 driver_factory 保留为普通低层对象装配入口。规则示例、订阅示例与研究 starter 已消费新的正式工厂；starter 的浏览与访谈分别由命名 driver factory 装配，阶段切换更新持久主体的 driver 名称。原 `ExposureMemory`、`StudyDriver` 示例转发壳删除。

## 二、激活

`ActivationContext` 与 `activation_scope` 用 AsyncExitStack 有序进入、逆序退出标准异步上下文管理器。规则、LLM 与测试中的第三驱动共同使用 session、认知材料、挂载、实际经历、Thread 身份、通用成功经历水位与 DriverResult。扩展进入先于 input_builder，扩展的延后 preparation 在必要输入记录之后执行。context.inputs 借用当前实际输入，context.messages 保存扩展新增认知；历史全文继续保存在原 Thread。

LLMDriver 的 memory 专属参数与 memory_input_through 结果键删除。成功水位位于通用 ActivationContext.through，提取在原 Thread 关闭前完成。completed、waiting 可写经验记忆，interview 默认跳过自动写入；incomplete、失败与取消不进入成功提取。规则自动写入要求研究者明确提供实际结构化经历，并可使用不依赖 Thread 的提取策略。memory_plugin 接受直接 extract 策略，三个策略开关独立。

## 三、记忆与文件

MemoryExtension 在主动访问启用时挂载 MemoryFiles；它采用普通 list/stat/read/revision 文件提供者合同。主体可以分页发现所有当前可见记忆，包含未被本次自动召回的条目。主动访问关闭后不挂载完整目录，自动召回的完整材料仍保留在认知与 Thread。范围读取依据现有规范 64 KiB zstd 帧读取所选原文字节，不读取记忆向量或完整正文。

目录统计的工作量由该主体当前可见记忆数量决定；所选条目元数据的总长度计算由各条目的规范帧数决定。完整提取明确随本 Thread 原文增长，与此前完整认知约束一致。恢复 driver 检查按现存主体目录执行一次，不进入逐次激活热路径。

## 四、提取与验证

结构合同统一归属 EXTRACT_MEMORIES_SCHEMA。jsonschema 完成数量、长度、字段、类型与重要度范围校验，既有 json-repair 解析入口保留。Pydantic AI StructuredDict、ToolOutput、output_validator 与原生 retries 取代手写提取回合循环，保留最多两次逻辑请求和原 4096 token 默认输出合同。提供方继续控制物理重试、容量、资源预算与请求留证。

剩余专用语义包括原 Thread 主体归属、最初系统背景、强制提取工具名与数量、空白记忆正文、原文快照释放、Thread 最终关闭前写入、SDK retry/tool 回执持久化，以及长度截断失败边界。Agent 请求经 request_model(model_messages=...) 的同步延迟 loader，在既有共享与端点许可授予后载入完整 typed 历史。capture_run_messages 提供 SDK 实际保留的历史列表，首次填充该列表与请求浅引用，后续校验纠正继续看到完整原文。直测证明提取仅有初始角色视图与一次 typed 历史快照；初始角色视图在物理请求前释放。三任务排队用例先复现许可前物化，修复后首轮排队及排队取消均无 typed 快照。首次 503 物理重试加重要度校验纠正的真实 SDK 离线 HTTP 用例证明三次物理请求的原文完整，物理重试水位相同、逻辑纠正水位增长且 typed 快照保持一次。首次许可后 SDK 历史在物理重试与校验纠正期间继续持有，工作量随该次实际完整 Thread 原文增长；没有新增独立 Semaphore。失败提取不创建成功 memory job，不执行嵌入写入。

先运行新增用例确认缺少 activation 模块导致收集失败，再实现。组合焦点命令覆盖 driver_extensions、actor_factory、memory、memory_activation、memory_activation_review、memory_snapshot_acceptance、memory_review、cognition、services、timing、native_tool_protocol、llm_paging_review、skill_starter、skill_starter_review 与 researcher_quickstart，110 项通过。后续增加共享 parser 修复语义用例，并覆盖失败提取最后工具回执；重新运行相关焦点。真实端点与整体确定性全量验收由整合工序汇总，本文的离线结果不推断真实端点长期稳定。

排队性能与 SDK 续轮历史的失败先行过程、具体绿色日志见 cognition-queue-red-green.md 与 cognition-queue-green.txt。最后认知、记忆、模型、services 与 starter 九组组合焦点 78 项通过；多个提取输出工具调用的一次纠正另通过。
