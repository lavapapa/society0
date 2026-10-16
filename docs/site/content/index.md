---
search:
  boost: 3
---
# Society0 Next

<p class="lead">搭好一个共享环境，让主体按规则或模型作出决定，把过程完整留下来。</p>

Society0 是一个用 Python 写的多主体仿真引擎，给需要研究社会过程的人使用。你负责定义有哪些主体、它们能看到什么、能做什么、时间怎么推进；引擎负责组织执行，并保存会话、事实、测量结果和完整步骤。对话、社交、记忆和文件工具都是内置能力，可以和你的机制放在同一个环境里。

**这套设计参考了 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/deepseek-harness) 的时空可组合性思路**：组件要能声明自己的依赖，也要能在退出时干净地撤销影响。DSH 的 everything-is-a-plugin 架构由 [Cordis](https://github.com/cordiverse/cordis) 支撑，设计论文是 [*A Programming Paradigm for Spatiotemporal Composability*](https://arxiv.org/abs/2608.25512)（Yifan Shi、Wei Zhang、Tianyi Cui，2026）。Society0 Next 把这套思路用在共享环境的插件组合、依赖声明和资源生命周期上，具体做法见[共享环境与机制](concepts/environment.md)。

[开始第一场仿真](start/quickstart.md){ .md-button .md-button--primary }
[安装 Next](start/installation.md){ .md-button }
[查看公开接口](reference/index.md){ .md-button }

## 你打算做什么

第一次运行不需要模型账号，用两位规则主体跑两个时点即可完成。

<div class="grid cards" markdown>

- **第一次使用**

    从两个主体的规则决定开始，读取真实结果，再从完整步骤继续运行。

    [安装](start/installation.md) → [第一次运行](start/quickstart.md) → [恢复与分叉](tutorial/recovery.md)

- **构建研究环境**

    决定事实由谁维护、主体能看到什么、行动如何执行，再把机制组合成一份运行计划。

    [运行计划](tutorial/first-plan.md) → [编写插件](guide/plugins.md) → [多来源认知](guide/cognition.md)

- **接入模型主体**

    配置模型服务、完整认知输入、行动完成条件和独立的记忆策略。

    [接入 LLM](tutorial/llm.md) → [主体记忆](tutorial/memory.md) → [模型与嵌入](guide/models.md)

- **运行与分析**

    边跑边看进度与会话，从可信的完整步骤恢复，导出能定位到原文的结果。

    [实时观察](tutorial/observation.md) → [存储管理](guide/storage.md) → [研究工作台](guide/workbench.md)

</div>

## 一场运行由什么组成

所有主体共用一个环境。插件往环境里添加服务和机制，Schedule 说明业务时间和阶段顺序，Runtime 逐步执行并在步骤结束时发布一次完整状态。

<div class="flow" aria-label="主体决定与完整步骤"><span>可得信息与认知输入</span><b aria-hidden="true">→</b><span>规则或模型决定</span><b aria-hidden="true">→</b><span>行动与规范事实</span><b aria-hidden="true">→</b><span>结果与完整步骤</span></div>

规则主体和模型主体走同一套信息与行动接口，所以换个驱动不必改环境。主体目录、认知来源、轮次对话、社交推荐都可以分别装配，也可以只留你研究需要的那几件。主体的决定、工具返回的原文和模型会话都会保存下来，恢复或建立对照分支时从完整步骤出发。

[阅读核心概念地图](start/concepts.md) · [理解共享环境与两类依赖](concepts/environment.md)

## 按你现在的问题查阅

学习路线、具体操作和接口查阅分开成三部分，方便在不同阶段找到深浅合适的内容。

| 你现在要做什么 | 阅读入口 |
|---|---|
| 跟着例子把一条运行链走完 | [教程](tutorial/index.md) |
| 弄清 Thread、Moment、完整步骤之间的关系 | [核心概念](concepts/index.md) |
| 换主体目录、组合插件、配置推荐或排查故障 | [任务指南](guide/index.md) |
| 查参数、状态、返回值、所有权和资源开销 | [接口参考](reference/index.md) |
| 找一个可以直接执行的例子 | [示例索引](guide/examples.md) |
| 用编码助手组织研究设计、试点和分析 | [研究 Skill](resources/research-skill.md) |

## 读之前需要知道的

本站讲的是独立重构线 **Next（猎龙）**。源码、依赖和本站的构建身份见[版本与来源](start/version.md)；产业环境有独立仓库和自己的领域文档，不在这里。

教程里的命令都在源码仓库根目录执行，每次尝试使用新的运行目录。标注“完整程序”的代码可以按给定文件名保存，标注“装配片段”的代码需要放进你自己的运行计划。模型地址、凭据和研究参数由你提供。

仿真跑出来的结果属于实验观察。要变成一个关于现实社会的结论，还需要说明设定与现实的对应关系，并用相应证据支撑，方法边界见[研究与证据](concepts/research.md)。

准备好后，从[第一次运行](start/quickstart.md)开始：执行一次、读出结果、再从完整步骤继续。
