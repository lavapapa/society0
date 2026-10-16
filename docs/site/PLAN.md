# Society0 Next 技术文档重构计划

本计划记录此次文档工程的范围、信息架构、内容责任与验收方式。产品正文面向首次接触项目的研究者、环境开发者及运行维护者；实施过程与审查记录留在本计划及外部验收目录。

## 一、依据与范围

历史站点位于工作区 outputs/society0-docs，使用 MkDocs Material，手写正文与根据合同抽取的参考合计52页。历史构建指向旧工作树，工具schema采用静态快照；旧站的社交语义资源默认值、依赖排序说明及部分源码链接需要更新。

本轮从远端 next 与本地 next 一致的 f99df0e6622f7bbd66dba90a33ae3a861306c0ac 建立 docs/next-handbook 专用工作树。站点源文件纳入 Core 的 docs/site，生成物交付至原输出目录。文档依据当前实现、确定性测试、公开示例和稳定合同，保持引擎及产业环境代码不变。

覆盖通用内核、公开运行入口、全部非产业内置机制、模型与记忆、文件与计算、观察与工作台以及研究skill。通用产业链插件及具体行业情景不纳入本站。研究skill中的学科设计指南作为方法资源介绍，与已实现机制分开。

## 二、参考与设计

已直接阅读以下官方文档，取其结构方法，并按 Society0 的使用路径调整：

- [Django 文档](https://docs.djangoproject.com/en/6.0/)：区分循序教程、主题解释、任务指南与精确参考；首页同时提供首次使用入口和主题导航。
- [FastAPI 教程](https://fastapi.tiangolo.com/tutorial/)：短主题逐步推进，完整可运行代码与操作结果紧邻；教程和参考分别服务学习与查阅。
- [uv 文档](https://docs.astral.sh/uv/)：以实际任务命名指南，把概念和配置/命令参考分开，提供明确交叉链接与下一步。

沿用 Material 的左侧主题导航、右侧页内目录、全文搜索、代码复制、明暗和移动抽屉。避免为文档新增应用框架。首页用读者任务组织入口，章节首页提供阅读路径。接口标题保留可搜索的英文符号，正文使用自然中文。

### 2.1 页面合同

- 教程：目标与先决条件 → 完整操作或可下载示例 → 预期结果与读取依据 → 单个关键解释 → 下一课。连续教程使用相同示例语境，明确完整程序与装配片段。
- 指南：一个具体任务 → 所需能力 → 操作顺序/代码 → 验证结果 → 相关故障与参考。领域规则由示例显式声明。
- 概念：一个稳定边界或心智模型 → 角色/状态/时间关系 → 容易混淆的差异 → 对实践的影响及指南入口。
- 参考：用途与导入路径 → 当前源码生成的签名/字段 → 输入、返回、状态、异常、所有权及成本语义 → 可定位源码符号与示例。无需复制教程全文。
- 资源：示例选择、研究skill工作方式、贡献及发布维护。历史测试次数、服务器状态与调试过程留在验收记录。

### 2.2 信息架构

下表是逐页撰写清单；路径相对于 content。保留有明确消费者的旧路径，新增页面按单一职责拆分。

| 分组 | 页面 | 主要职责 |
|---|---|---|
| 首页 | index.md | 项目定位、三类读者路径、核心运行链及开始入口 |
| 开始 | start/index.md | 学习路径与先决条件 |
| 开始 | start/installation.md | 显式选择Next源码、基础环境与功能依赖 |
| 开始 | start/quickstart.md | 无模型的首次运行、读取真实结果 |
| 开始 | start/concepts.md | 最小词汇及运行链地图，导向概念专题 |
| 开始 | start/version.md（生成） | 同一源码构建身份、支持范围与源码链接 |
| 教程 | tutorial/index.md | 四条可执行学习路线 |
| 教程 | tutorial/first-plan.md | 自己的规则运行工厂与公开RunPlan |
| 教程 | tutorial/actors.md | 持久主体资料、驱动标签及独立目录 |
| 教程 | tutorial/conversation.md | 两个轮次对话机制及行动、结果 |
| 教程 | tutorial/llm.md | 模型驱动、完整输入和行动循环 |
| 教程 | tutorial/memory.md | 独立配置写入、召回、主动工具 |
| 教程 | tutorial/files.md | 从根目录发现资料、字节续读和数据查询 |
| 教程 | tutorial/actions.md | 发现、描述、提交与完成语义 |
| 教程 | tutorial/results.md | 指标、表、分页与完整原文 |
| 教程 | tutorial/recovery.md | 完整点恢复及新目录继续 |
| 教程 | tutorial/observation.md | 运行诊断与完整结果读取 |
| 概念 | concepts/index.md | 按问题选择解释专题 |
| 概念 | concepts/environment.md | 共享环境、机制插件与两类依赖图 |
| 概念 | concepts/activation.md | Actor、Driver、Session、Moment与激活 |
| 概念 | concepts/cognition.md | 完整Thread、增量认知位置及保存后effects |
| 概念 | concepts/information.md | 授权目录、原文、分页与版本绑定 |
| 概念 | concepts/complete-steps.md | 当前状态、完整点、恢复、只读分析与外部副作用 |
| 概念 | concepts/memory.md | 权威记忆、向量索引、时间与生命周期 |
| 概念 | concepts/concurrency.md | 业务独立性、请求并发与进程计算 |
| 概念 | concepts/research.md | 规则/模型假设、软件验证与研究证据 |
| 指南 | guide/index.md | 按任务导航 |
| 指南 | guide/plugins.md | 创建机制、声明依赖、注册与释放资源 |
| 指南 | guide/composition.md | presets、includes、命名与服务替换 |
| 指南 | guide/actor-directory.md | 外部主体目录及恢复驱动解析 |
| 指南 | guide/cognition.md | 多SourceSpec组合、独立位置与effects |
| 指南 | guide/information.md | SQL资料、文档及注册生命周期 |
| 指南 | guide/schedule.md | 业务时间、阶段顺序与未完成策略 |
| 指南 | guide/builtin.md | plain/round-robin/social能力选择与工厂映射 |
| 指南 | guide/social.md | 社交叶插件、推荐策略、认知与曝光 |
| 指南 | guide/models.md | 提供方、profile、模型选择与嵌入 |
| 指南 | guide/subscription.md | SIWC及Codex CLI OAuth配置 |
| 指南 | guide/workspace.md | 私有文件、环境资料与Shell |
| 指南 | guide/datasets.md | 不可变数据批次及结果引用 |
| 指南 | guide/storage.md | SQL事务、工件、恢复与清理 |
| 指南 | guide/parallel.md | 独立激活、请求配额、进程计算 |
| 指南 | guide/workbench.md | 从完整工件导出可追溯工作台 |
| 指南 | guide/troubleshooting.md | 从诊断定位安装/装配/运行/恢复问题 |
| 资源 | guide/examples.md | 按目标索引公开可执行示例 |
| 资源 | resources/research-skill.md | 安装完整skill及研究工作循环 |
| 资源 | resources/contributing.md | 源码布局、确定性验证、文档更新与发布 |
| 参考 | reference/index.md | 按公开能力分组查找符号 |
| 参考 | reference/installation.md | 可选依赖与平台合同 |
| 参考 | reference/plugin.md | Plugin、PluginContext、PluginHost |
| 参考 | reference/composition.md | compose及rule/llm presets |
| 参考 | reference/actor.md | 主体记录、目录、ActorStore及工厂 |
| 参考 | reference/runtime.md | Runtime、Session、Phase、调度与selection |
| 参考 | reference/runner.md | RunContract、RunPlan、CLI与产物 |
| 参考 | reference/cognition.md | CognitiveInput、SourceSpec及激活扩展 |
| 参考 | reference/llm.md | LLMPolicy、LLMDriver、决策/访谈 |
| 参考 | reference/models.md | 模型/嵌入提供方、选择及资源计量 |
| 参考 | reference/memory.md | Memory、策略、扩展、提取与迁移 |
| 参考 | reference/services.md | Thread/Memory服务工厂与生命周期 |
| 参考 | reference/interaction.md | Ref、Query、Information、Actions |
| 参考 | reference/information.md | SQLInformation、DatasetSpec、DocumentSpec |
| 参考 | reference/filesystem.md | ActorFiles、InformationFiles、命名空间 |
| 参考 | reference/shell.md | ShellSession及命令入口 |
| 参考 | reference/workspace.md | WorkspaceStore、Lease及工厂 |
| 参考 | reference/thread.md | ThreadStore及typed模型消息 |
| 参考 | reference/storage.md | StageStore、ReadView、Writer、StageReader |
| 参考 | reference/cold-datasets.md | Datasets与封存批次 |
| 参考 | reference/storage-lifecycle.md | restore、prepare_readonly、export、清理 |
| 参考 | reference/request-limit.md | 请求许可与并发资源 |
| 参考 | reference/native-json.md | 编码合同及数据类型 |
| 参考 | reference/results.md | StepResult、Results、表与分页信封 |
| 参考 | reference/observation.md | Observation、HTTP、完整视图 |
| 参考 | reference/diagnostics.md | 行动统计、资源用量与进度 |
| 参考 | reference/social.md | 社交叶、领域服务、排序与认知 |
| 参考 | reference/round-robin.md | 轮次对话、公开资料与行动 |
| 参考 | reference/compute.md | ProcessCompute与进程池生命周期 |
| 参考 | reference/workbench.md | RunSelection、export_payload与HTML |
| 参考 | reference/tools.md（生成） | 实际LLMDriver工具schema与模式差异 |

## 三、实现与验收

先确定导航和页面责任，再逐页依据当前源码撰写。源代码签名、版本和工具schema由构建读取权威源码生成；语义正文人工编写并链接实际符号。取消旧生成器中的历史字段替换、合同正文自动删段与静态schema输入。构建只生成站点与包，不调用模型服务。

检验包括：页面及导航覆盖、内部链接和锚点、源码符号存在、Python示例语法及核心教程真实无模型运行、生成schema与当前工具一致、严格MkDocs构建、HTTP/file中文搜索、代码复制、明暗与手机目录、离线ZIP解压。最终独立执行者按新用户和查阅者路径审查实际页面，不重复单元测试。真实模型与嵌入调用不在本轮执行预算内，相关配置示例须明确前提。

任务提交在专用分支中保留；完成后按仓库规则合入Next并推送。可部署静态站点与离线包交付到原输出位置；公网部署地址须有实际部署证据后再报告。
