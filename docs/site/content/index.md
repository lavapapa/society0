---
search:
  boost: 3
---
# Society0 Next

<p class="lead">构建共享环境，让主体行动，保存可追溯的研究过程。</p>

Society0 是 Python 多主体仿真引擎。你定义研究中的主体、可得信息、行动与时间顺序，使用规则或大模型驱动决定；引擎组织执行，保存会话、事实、测量结果与完整步骤。内置对话、社交、记忆和文件能力可以组合进同一个环境。

**设计启发与致谢：Society0 Next 从 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/deepseek-harness) 的时空可组合性理念获得灵感与参考，并据此设计和实现共享环境中的插件组合与生命周期管理。** DSH 以 Cordis 支撑 everything-is-a-plugin 架构；相关设计论文 [*A Programming Paradigm for Spatiotemporal Composability*](https://arxiv.org/abs/2608.25512)（Yifan Shi、Wei Zhang、Tianyi Cui，2026）为这一路线提供了重要参考。具体落地见[共享环境与机制](concepts/environment.md)。

[开始第一场仿真](start/quickstart.md){ .md-button .md-button--primary }
[安装 Next](start/installation.md){ .md-button }
[查看公开接口](reference/index.md){ .md-button }

## 一、选择你的路径

从当前任务进入，再按需深入。第一次运行使用规则主体，无需模型账号。

<div class="grid cards" markdown>

- **第一次使用**

    从两个主体的规则决定开始，读取真实结果，再从完整步骤继续运行。

    [安装](start/installation.md) → [第一次运行](start/quickstart.md) → [恢复与分叉](tutorial/recovery.md)

- **构建研究环境**

    定义事实由谁维护、主体能看到什么、行动如何执行，并将机制组合为运行计划。

    [运行计划](tutorial/first-plan.md) → [编写插件](guide/plugins.md) → [多来源认知](guide/cognition.md)

- **接入模型主体**

    配置模型提供方、完整认知输入、行动完成条件与独立的记忆策略。

    [接入 LLM](tutorial/llm.md) → [主体记忆](tutorial/memory.md) → [模型与嵌入](guide/models.md)

- **运行与分析**

    观察当前进度与会话，从可信完整点恢复，导出可定位原文的研究结果。

    [实时观察](tutorial/observation.md) → [存储管理](guide/storage.md) → [研究工作台](guide/workbench.md)

</div>

## 二、一场运行如何组成

多个主体共享环境。插件提供环境内部的服务与机制，Schedule 声明业务时间和阶段顺序，Runtime 执行步骤并发布完整边界。

<div class="flow" aria-label="主体决定与完整步骤"><span>可得信息与认知输入</span><b aria-hidden="true">→</b><span>规则或模型决定</span><b aria-hidden="true">→</b><span>行动与规范事实</span><b aria-hidden="true">→</b><span>结果与完整步骤</span></div>

规则与模型驱动使用同一套信息和行动接口。插件可组合主体目录、认知来源、对话、社交推荐和研究者自定义机制。工具结果与主体原始会话保持可追溯；恢复与对照分支从可信完整步骤出发。

[阅读核心概念地图](start/concepts.md) · [理解共享环境与两类依赖](concepts/environment.md)

## 三、按问题查阅

本站将学习过程、具体操作与接口查阅分开，便于在不同阶段找到合适深度的内容。

| 你现在要做什么 | 阅读入口 |
|---|---|
| 跟着例子完成一条运行链 | [教程](tutorial/index.md) |
| 判断Thread、Moment、完整点的关系 | [核心概念](concepts/index.md) |
| 更换目录、组合插件、配置推荐或处理故障 | [任务指南](guide/index.md) |
| 查参数、状态、返回、所有权和资源成本 | [接口参考](reference/index.md) |
| 找到一个可执行的公开例子 | [示例索引](guide/examples.md) |
| 用编码助手组织研究设计、试点和分析 | [研究 Skill](resources/research-skill.md) |

## 四、使用约定

本指南面向独立重构路线 **Next（猎龙）**，源码、依赖与本站构建身份见[版本与来源](start/version.md)。覆盖通用内核、内置基础机制和非产业扩展；产业环境具有独立仓库和领域文档。

教程中的命令从源码仓库根目录执行。每次尝试使用新的运行目录；“完整程序”可按给定文件名保存，“装配片段”需放入相应运行计划。模型端点、凭据和真实研究参数由使用者提供。具体社会结论需要实验设计、校准与现实证据支持，方法边界见[研究与证据](concepts/research.md)。

准备好后，从[第一次运行](start/quickstart.md)完成“执行—读取—继续”的最小闭环。
