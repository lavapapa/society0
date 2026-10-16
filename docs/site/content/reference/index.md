# 接口参考

按当前正在实现的能力查找接口。页面中的签名从同一源码树自动提取，正文说明调用者需要承担的语义、生命周期与资源成本；版本身份见[版本与来源](../start/version.md)。学习完整运行路线请从[教程](../tutorial/index.md)开始。

## 一、组合与执行

这组接口把研究配置转为可执行计划，决定谁拥有状态、谁提供服务，以及业务步骤怎样推进。

| 能力 | 公开入口 |
|---|---|
| Python与可选依赖 | [安装参考](installation.md) |
| 声明插件、依赖、初始化与服务 | [Plugin、PluginContext、PluginHost](plugin.md) |
| 装配共享状态及预设 | [compose、rule_preset、llm_preset](composition.md) |
| 持久身份与驱动目录 | [ActorRecord、ActorDirectory、ActorStore](actor.md) |
| 阶段执行与主体激活 | [Runtime、Session、Phase、Schedule](runtime.md) |
| 冻结运行合同与命令行 | [RunPlan、run_plan](runner.md) |

## 二、主体认知与决定

输入、决定、会话与记忆分别承担不同职责。模型资源独立配置，保存过程由运行与存储合同约束。

| 能力 | 公开入口 |
|---|---|
| 认知输入及独立来源位置 | [CognitiveInput、SourceSpec](cognition.md) |
| 决策与访谈策略 | [LLMDriver、LLMPolicy](llm.md) |
| 模型实际收到的工具声明 | [Agent工具参数](tools.md) |
| 提供方、模型选择与嵌入 | [ModelProvider、EmbeddingProvider](models.md) |
| 写入、召回、主动工具与提取 | [Memory、MemoryPolicy、MemoryExtension](memory.md) |
| Thread与记忆服务装配 | [服务工厂](services.md) |
| 完整会话和调用原文 | [ThreadStore](thread.md) |

## 三、信息与文件

环境资料和行动都在主体绑定的权限上下文中使用。分页保留总数和继续读取入口，文件接口提供目录与原文的统一访问路径。

| 能力 | 公开入口 |
|---|---|
| 资源引用、目录发现与领域行动 | [Ref、Query、Information、Actions](interaction.md) |
| SQL记录与文档投影 | [SQLInformation、DatasetSpec、DocumentSpec](information.md) |
| 主体文件命名空间 | [ActorFiles、InformationFiles](filesystem.md) |
| Shell及数据、行动命令 | [ShellSession](shell.md) |
| 主体私有持久文件 | [WorkspaceStore、WorkspaceLease](workspace.md) |

## 四、权威状态与资源

当前读写、完整点与不可变工件有不同用途。使用这些接口时需同时理解借用生命周期、恢复边界和数据物化成本。

| 能力 | 公开入口 |
|---|---|
| 事务、读取与完整步骤 | [StageStore、Writer、ReadView](storage.md) |
| 恢复、只读分析、导出与清理 | [存储生命周期](storage-lifecycle.md) |
| 大规模不可变数据 | [Datasets](cold-datasets.md) |
| JSON与压缩编码 | [原生编码](native-json.md) |
| 模型服务的请求许可 | [共享请求限制](request-limit.md) |
| 独立CPU计算 | [ProcessCompute](compute.md) |

## 五、机制、结果与观察

内置机制提供可组合的基础环境行为，结果与观察接口服务于事后分析和运行诊断。

| 能力 | 公开入口 |
|---|---|
| 配对、轮次与消息历史 | [RoundRobin](round-robin.md) |
| 社交叶插件、排名与认知 | [Social](social.md) |
| 指标、表与结果分页 | [StepResult、Results](results.md) |
| 当前诊断、完整视图与HTTP | [Observation](observation.md) |
| 行动、请求、时间与用量 | [诊断指标](diagnostics.md) |
| 从完整工件导出页面数据 | [RunSelection、export_payload](workbench.md) |

辅助实现按实际消费者归入上述页面。以公开运行入口与明确服务为扩展点，可减少对内部调度和存储细节的耦合；具体组合方式见[预设配方与服务替换](../guide/composition.md)。
