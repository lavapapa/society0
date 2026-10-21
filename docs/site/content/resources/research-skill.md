# 用研究 skill 组织实验

把问题告诉 society0 Skill，助手会结合两轮并行搜索与领域知识，提出主体、环境和影响因素的选择，再与你确定实验问题和平行世界，自动构建、运行并解释结果。Python 包实际执行仿真；Skill 提供协作步骤、领域参考和起步程序。已有运行可以直接分析或恢复，首次缺少环境时再准备安装。

## 把 skill 装进编码助手

从本站对应的源码取得完整 [skill 目录](https://github.com/lavapapa/society0/tree/@COMMIT@/skill)。将它作为名为 society0 的 skill 放入所用编码助手支持的技能目录，保留入口、references、assets 和 scripts 的相对结构。具体发现位置按该助手的配置选择。

安装后请助手加载 society0，确认能读取 [SKILL.md](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/SKILL.md)、[安装准备](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/references/installation.md)、[运行快速说明](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/references/runtime-quickstart.md)与[完整起步程序](https://github.com/lavapapa/society0/blob/@COMMIT@/skill/assets/minimal_experiment.py)。首次安装依次核对源码、解释器与依赖、模型、初始化、真实试跑和恢复。正式使用从开始加载独立的 society0-workbench，由它指导工作台操作；缺少入口时记录接入待办。

可以这样开始：

> 使用 Society0 skill，帮我把“资料来源是否影响消息可信度判断”设计成一个两轮小实验。先列出环境可见信息、参与者能执行的行动、对照条件和测量，再给出无模型结构检查与真实运行的步骤。

## 完成一个小试验

先确认参与者实际能看到什么，再设计他们可以做的行动和环境承担的后果。模型描述中的机构、资源或消息无需都建成可决策主体；明确哪些实体拥有感知与行动能力，哪些只是记录。

起步程序包含浏览消息、查看详情行动、记忆与结构化可信度测量。按[示例指南](../guide/examples.md)设置实际模型及嵌入环境后，先运行初始化检查，再用新目录执行两轮：

```bash
uv run python skill/assets/minimal_experiment.py \
  --check --run-dir runs/research/check
uv run python skill/assets/minimal_experiment.py \
  --run-dir runs/research/pilot
```

`--check` 模式不调用提供方，但仍需要完整资源配置与依赖。正式运行会使用真实模型和嵌入服务。当前起步程序启用自动写入、自动召回和主动记忆工具；成功浏览由记忆扩展自动提取，访谈默认跳过自动写入。三类记忆行为在[记忆教程](../tutorial/memory.md)中分别配置，改变它们时说明研究上的含义。

## 按主题选择方法资源

简单的两轮介绍可从起步程序与运行说明开始。进入实质研究设计或论文改编后，按主题选择 skill 中的领域指南：传播与社交媒体、访谈与调查、治理与制度、教育、法律、公共健康、消费市场、经济与金融等。

这些指南帮助确定材料准备、主体资料、信息可见范围、干预、对照、校准和解释边界。领域方法需要由研究计划和机制代码落实；你在环境里能直接用的能力从[内置机制](../guide/builtin.md)选择，需要新增规则时按[插件指南](../guide/plugins.md)实现。

改编论文时保留原文证据与复现范围，分别记录已复现的机制、为小试验作出的简化及尚无数据支持的部分。模型给出的理由可作为质性线索，因果解释需要对照与重复观察。

## 检查真实结果并迭代

让助手先读取真实阶段结果、Thread 原文和记忆，再解释一次运行。用小试验核对行动是否发生、原文是否完整、测量是否属于对应主体；运行成功之后继续检查研究问题所需的基线、重复与现实数据口径。

设计初稿、配置选择、运行状态和结果按 society0-workbench 的实际要求交付。世界可以独立初始化、配对控制变量或从可信完整点分叉，选择依据是要回答的实验问题。扩大规模前根据实际请求用量与资源成本安排预算；[研究边界](../concepts/research.md)说明如何区分软件验证、仿真观察与现实证据。
