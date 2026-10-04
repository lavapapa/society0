# 引擎与插件

一个运行拥有共享环境、主体身份与逻辑时间。插件实现环境内部机制，CodeSchedule 明确业务时序；插件依赖表达服务安装关系，并影响步骤钩子的登记顺序；阶段顺序和主体活动的独立性由研究计划声明。

## 一、装配

`Plugin(name, requires, install, schema=..., initialize=...)` 声明依赖与静态状态。`compose` 先汇集 schema 和初始化，在一个 StageStore 中发布根，再按依赖安装服务。异步外部数据准备使用 `prepare` 上下文管理器返回同步 initializer。恢复从完整点创建新运行，重新安装本次资源服务，保留权威状态。

标准工厂包括 actor_plugin、interaction_plugin、thread_plugin、memory_plugin、model_plugin、embedding_plugin、workspace_plugin、results_plugin、dataset_plugin、runtime_plugin。按实际消费者选用，普通规则不需要模型、记忆或 shell。领域机制通过 PluginContext.require 获取显式依赖服务，provide 暴露自身服务，on_close 归还资源，on_step 注册完整步骤钩子。

## 二、交互

ActorRecord 保存人格、主观状态、配置和角色；ActorStore 按实际主体按需加载。Driver 可以是规则或 LLM，使用绑定身份的 Session。共享 Information 提供目录、分页数据与原文范围；Actions 注册按目标类型发现的模板，执行时重新检查资格。信息读取资格与领域允许的行动由机制定义。

LLMDriver 保留完整 Thread，提供 data/action 元工具，按需接 shell。Memory 以 SQL 正文与原始向量为权威，Chroma 为可重建检索投影。各激活分别冻结记忆开关与模型选择。

## 三、运行

Runtime 以阶段及完整步骤组织活动；CodeSchedule、RunPlan、run_plan 提供研究计划入口。StepResult 保存指标、原始结构和表；观察服务在独立进程读取当前诊断或固定完整视图。当前权威表、不可变正文、JSON 元数据和私有 workspace 各有明确归属，插件缓存作为可重建运行资源。机制如何对应世界事实、主体视野和行动，见 [双机制设计例](environment-design.md#从双机制例子构建自己的世界)。

完整使用例在 `../assets/minimal_experiment.py`。核对源码从 `src/society0/kernel/` 与 `src/society0/plugins/` 开始。
