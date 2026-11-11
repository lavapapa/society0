---
name: society0
description: "面向社会科学、传播、经济、组织、政策与行为研究者的 Society0 研究技能：把模糊的日常问题变成可运行的社会仿真实验，选择 Core/Social 插件与 profile，用 society0 CLI 检查、启动、恢复并读取运行结果，并用同一起点的平行运行做控制变量比较。用户提到社会仿真、研究设计、实验搭建、ODD、主体与信息暴露、profile 或插件选择、数据准备、试跑、结果分析、分叉、平行世界、种子设置时使用。"
---

# Society0

这个 skill 帮研究者把一个问题变成可运行、可检查、可比较的社会仿真实验。环境先于主体：先写清谁能看到什么、能做什么、环境会记录和约束什么，再决定哪些主体需要语言判断。

## 先看哪里

- 第一次把模糊问题变成实验：读 [references/from-question-to-experiment.md](references/from-question-to-experiment.md)。它按“澄清问题、找因素和路径、确定主体层和微观群体、写 ODD、选插件、补数据、小规模试跑”逐步给出产出物、判断标准和常见坑。
- 要比较干预或参数、开平行世界：读 [references/causal-branches.md](references/causal-branches.md)。它给出从同一完整点分叉的真实命令、种子控制、比较口径和结论边界。
- 需要建立研究立场、证据边界和有效性检查：读 [references/research-design.md](references/research-design.md)。
- 需要选环境、信息暴露和行动：读 [references/environment-design.md](references/environment-design.md)。
- 需要设计主体、规则基线与 LLM 主体：读 [references/agent-design.md](references/agent-design.md)。
- 需要 profile、CLI、目录、锁文件、运行与恢复：读 [references/runtime-and-cli.md](references/runtime-and-cli.md)。
- 需要写本地插件或自定义机制：读 [references/plugin-development.md](references/plugin-development.md)。
- 需要按领域找研究模式和最小可行实验：读 [references/study-patterns.md](references/study-patterns.md)，再按主题打开下面的领域指南。
- 需要解读运行输出、做定量与定性分析：读 [references/run-monitor-analyze.md](references/run-monitor-analyze.md)。

领域指南：

- 传播与社交媒体：[communication-social-media-simulation-design.md](references/communication-social-media-simulation-design.md)
- 访谈、问卷与审议：[interview-survey-deliberation-simulation-design.md](references/interview-survey-deliberation-simulation-design.md)
- 治理、制度与公共政策：[governance-institution-public-policy-simulation-design.md](references/governance-institution-public-policy-simulation-design.md)
- 经济与金融：[economics-finance-simulation-design.md](references/economics-finance-simulation-design.md)
- 组织、工作流与协作：[organization-workflow-collaboration-simulation-design.md](references/organization-workflow-collaboration-simulation-design.md)
- 教育、公共卫生与城市社区：[education-public-health-city-simulation-design.md](references/education-public-health-city-simulation-design.md)
- 法律、司法与犯罪：[law-justice-crime-simulation-design.md](references/law-justice-crime-simulation-design.md)
- 国际关系、危机与安全：[international-relations-conflict-security-simulation-design.md](references/international-relations-conflict-security-simulation-design.md)

## 两条主线

### 一、从问题到可运行实验

按下面的顺序推进，不要先列一堆主体再补环境：

1. 把模糊问题改写成可检验的研究问题，写清比较对象和结果变量。
2. 找出因素、机制路径和可能的替代解释，确定哪些因素作为条件、哪些固定。
3. 划主体层：谁感知、谁决定、谁行动、谁承担后果；哪些只是资源、记录、机构、地点或流程位置。
4. 写 ODD，把目的、实体、状态、时间尺度、过程、交互、随机性和观测写清。
5. 选插件与 profile：规则基线用 Core，信息暴露和互动用 Social，语言判断用 LLM driver。
6. 补数据：主体画像、初始状态、刺激材料、网络边、测量表；缺什么就明确写“缺”，不要用叙事填满。
7. 先跑最小规模，检查主体看到的是否就是设计中的材料、行动是否真的发生、结果是否能从记录中重算。

每一步的详细产出物和判断标准见 [references/from-question-to-experiment.md](references/from-question-to-experiment.md)。

### 二、用控制变量探索因果

因果比较不是“跑两次看看”，而是固定同一起点、主体、数据和种子，只改一个干预或参数：

1. 先让来源运行走到一个可用的完整点，并记录它的点 id。
2. 用分叉接口从同一点创建目标运行，保留源 lineage 和种子。
3. 目标配置只改一个因素；控制组保持原配置。
4. 比较目标运行的结果、行动记录和主体状态，写清样本数、种子数、随机性和测量口径。
5. 结论只覆盖这次比较能支持的范围；模型自述的原因只作为线索。

真实可用的命令、目标运行的读取方式和做不到时的替代做法见 [references/causal-branches.md](references/causal-branches.md)。

## 工作循环

1. **澄清**：用研究语言写出问题、条件、结果和反例。一次只问会改变设计的问题。
2. **证据边界**：说明手头材料支持什么、不支持什么；不要把生成文本当成测量值。
3. **环境**：写清信息暴露、行动、环境约束和记录。推荐、可见性、权限、价格、配额等是实验条件，不是中性管道。
4. **主体**：先决定规则基线和 LLM 主体的分工，再写人格、状态和记忆。隐藏条件不能进入主体可见状态。
5. **协议**：把 tick 定义成研究时间单位；写清每个 tick 的阶段、顺序、干预发生的位置和测量时点。
6. **装配**：从现有 profile 复制一份，改配置而不是改运行中的 run。每次尝试使用新目录。
7. **试跑**：先减少主体数和 tick 数，保留对照；检查行动是否完成、失败原因和预算。
8. **分析**：先报告实际发生的比较和样本量，再提出机制；把模拟输出与经验证据分开。

## 最小示例

`assets/minimal-study/` 是一个可运行的规则驱动示例：两名主体，三轮信息更新，另有一份只改第二轮刺激材料的对照 profile，用来演示同一起点的分叉比较。

```sh
# 在示例目录中准备锁文件并检查配置
node lock.mjs
society0 check --profile profile.json --json
society0 run start --profile profile.json --dir runs/source --ticks 1 --detach --json
society0 run status --dir runs/source --json
society0 run result --dir runs/source --json
```

完整步骤、分叉命令和输出解释见 [assets/minimal-study/README.md](assets/minimal-study/README.md)。

## 命令速查

```sh
society0 check --profile <profile.json> --json
society0 run start --profile <profile.json> --dir <run-dir> [--ticks N] [--patches <patch.json>] --detach --json
society0 run status --dir <run-dir> --json
society0 run result --dir <run-dir> --json
society0 run resume --dir <run-dir> --detach --json
```

`check` 只做静态装配检查，不创建 run、不连接模型。`run start` 在启动前重复同一检查；不通过时不会创建 run 目录。`--ticks` 是上限，不保证跑满；来源先耗尽时提前结束。`run resume` 从已发布的完整点继续；没有可用完整点时会报错并保留现场。

## 边界

- 不把 Society0 描述成“传统 ABM 加一个 LLM 行为函数”。它把可见证据、行动、环境后果和记录放在环境里，语言判断放在需要它的主体上。
- 不让主体看到研究者的条件标签、真值或预期结论。
- 不用一次运行证明机制。检查重复种子、重复运行、替代解释和测量口径。
- 不把模型写出的理由当成因果证据；因果判断来自对照、固定条件和可重算的记录。
- 不在同一个 run 目录里混多次实验。配置改变就用新的 profile 和新的 run。
- 不擅自扩大规模。先让最小实验的暴露、行动和记录都说得通，再加主体、记忆、网络和 tick。
