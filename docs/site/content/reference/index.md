# 接口参考

这里按能力汇总公开接口，方便你在确定要调用什么之后回来查参数、返回值、状态和资源开销。页面里的签名由构建过程从同一源码树自动提取，正文补上调用者要自己承担的部分：谁持有资源并在退出时关闭、哪些状态由运行保存、出错时抛哪类异常。本站的版本身份见[版本与来源](../start/version.md)；要按顺序走完一次运行，请从[教程](../tutorial/index.md)开始。

## 组合与执行

这一组接口把你写下的研究配置变成可以执行的计划，并决定谁持有状态、谁提供服务、业务步骤按什么顺序推进。

| 能力 | 公开入口 |
|---|---|
| Python 版本与可选依赖 | [安装参考](installation.md) |
| 声明插件、依赖、初始化与服务 | [Plugin、PluginContext、PluginHost](plugin.md) |
| 装配共享状态与预设配方 | [compose、rule_preset、llm_preset](composition.md) |
| 持久身份与驱动目录 | [ActorRecord、ActorDirectory、ActorStore](actor.md) |
| 阶段执行与主体激活 | [Runtime、Session、Phase、Schedule](runtime.md) |
| 一次运行的配置、计划与命令行 | [RunPlan、run_plan](runner.md) |

## 主体的认知与决定

输入、决定、会话和记忆各管一件事，可以分别装配。模型资源单独配置；哪些内容会被保存下来，由运行过程和存储接口决定。

| 能力 | 公开入口 |
|---|---|
| 认知输入与各来源的独立位置 | [CognitiveInput、SourceSpec](cognition.md) |
| 决定与访谈两种策略 | [LLMDriver、LLMPolicy](llm.md) |
| 模型实际收到的工具声明 | [Agent工具参数](tools.md) |
| 提供方、模型选择与嵌入 | [ModelProvider、EmbeddingProvider](models.md) |
| 写入、召回、主动工具与提取 | [Memory、MemoryPolicy、MemoryExtension](memory.md) |
| Thread 与记忆服务的装配 | [服务工厂](services.md) |
| 完整会话与调用原文 | [ThreadStore](thread.md) |

## 信息、文件与行动

环境资料和行动都在主体绑定的权限范围内使用。分页保留总数和继续读取的入口，文件接口把目录浏览和原文读取放在同一条路径上。

| 能力 | 公开入口 |
|---|---|
| 资源引用、目录发现与领域行动 | [Ref、Query、Information、Actions](interaction.md) |
| SQL 记录与文档投影 | [SQLInformation、DatasetSpec、DocumentSpec](information.md) |
| 主体文件命名空间 | [ActorFiles、InformationFiles](filesystem.md) |
| Shell 与数据、行动命令 | [ShellSession](shell.md) |
| 主体私有持久文件 | [WorkspaceStore、WorkspaceLease](workspace.md) |

## 权威状态与资源开销

当前读写、完整步骤和不可变工件各有各的用途。调用这些接口时要一并考虑借用的生命周期、恢复从哪里开始，以及一次读取会实际取出多少数据。

| 能力 | 公开入口 |
|---|---|
| 事务、读取与完整步骤 | [StageStore、Writer、ReadView](storage.md) |
| 恢复、只读分析、导出与清理 | [存储生命周期](storage-lifecycle.md) |
| 大规模不可变数据 | [Datasets](cold-datasets.md) |
| JSON 与压缩编码 | [原生编码](native-json.md) |
| 模型服务的请求许可 | [共享请求限制](request-limit.md) |
| 独立进程上的 CPU 计算 | [ProcessCompute](compute.md) |

## 内置机制、结果与观察

内置机制提供可以组合的基础环境行为，结果与观察接口分别服务于事后分析和运行诊断。

| 能力 | 公开入口 |
|---|---|
| 配对、轮次与消息历史 | [RoundRobin](round-robin.md) |
| 社交叶插件、排名与认知 | [Social](social.md) |
| 指标、表与结果分页 | [StepResult、Results](results.md) |
| 当前诊断、完整视图与 HTTP 服务 | [Observation](observation.md) |
| 行动、请求、时间与用量 | [诊断指标](diagnostics.md) |
| 从完整工件导出页面数据 | [RunSelection、export_payload](workbench.md) |

辅助实现按实际使用它的组件归入上述页面。扩展时以公开的运行入口和明确的服务为接入点，可以少依赖内部调度和存储细节；具体做法见[预设配方与服务替换](../guide/composition.md)。
