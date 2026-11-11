# 把问题变成实验

仿真平台不会替研究者决定问题是否成立。它提供主体、世界、时间、插件和记录；实验设计仍然由人完成。下面是一条从日常问题到可运行实验的路径。

## 1. 把问题写成可观察的差异

先写清楚要比较什么，再决定建什么世界。例如：

- “信息在关系网络中传播得更快，还是通过推荐传播得更快？”
- “同一群体在配对轮换和自由互动下，观点分布有什么不同？”
- “增加一次外部事件，会改变后续多少主体的行为？”

把问题转成至少一个可测量结果：发帖数、被传播到的成员数、测量回答的分布、行动回执数量、完成一次任务所需的 tick 数。没有可测量结果的问题，通常会退化成功能演示。

同时写下不准备解释的部分。模型驱动的行为有随机性；一次 run 的差异不能自动归因于干预。

## 2. 列出因素和路径

把可能影响结果的因素分组：

- 世界因素：主体数量、关系结构、初始资料、外部事件。
- 机制因素：传播策略、推荐来源、配对方式、感知范围。
- 时间因素：tick 长度、事件时间、采样时机。
- 模型因素：provider、上下文窗口、输出预算、工具集合。

从这些因素中选一个作为本次要改变的量，其余固定。因素之间会互相作用；如果一次改两个，结果无法区分来源。

## 3. 确定主体层和微观群体

主体层是实验的分析单位：个人、配对、群体、整个网络。微观群体决定主体和谁互动、看见谁、被谁影响。

在 Society0 里，主体层通常由这些数据表达：

- `world.actors`：主体 id、属性和来源；
- Social 的 `pairingGroups` 和拓扑边：谁和谁在一组；
- 推荐来源和感知范围：主体能看到哪些内容；
- proposal 序列或事件源：谁在什么时间被激活。

主体层要能回答“差异发生在谁身上”。如果只看全局总数，往往会掩盖群体内部的分布变化。

## 4. 写 ODD

ODD 用来把设计说清楚，避免实现细节替代研究假设：

- **目的**：这次仿真要回答什么问题，不回答什么。
- **实体**：主体、群体、内容、关系、外部事件各有哪些类型。
- **状态变量**：每个主体和世界保存哪些会变化的量。
- **过程**：一 tick 内发生什么，感知、决策、行动、写入的先后顺序。
- **调度**：哪些阶段串行，哪些可以并行，激活如何产生。
- **观察**：哪些量在什么时间被记录，记录到 Results 还是只读观察。
- **终止**：达到 tick 上限、事件结束、没有 proposal，还是遇到未完成激活暂停。

ODD 不需要写成论文长度；它要足够让别人根据同一份 profile 和数据重建实验。

## 5. 选择插件和 profile

从最小可运行组合开始：

| 目标 | 起点 | 追加 |
| --- | --- | --- |
| 固定顺序的基线 | `rule-sqlite` | 规则处理器、proposal 序列 |
| 外部事件驱动 | `event-sqlite` | 事件批次、事件时钟 |
| 关系和内容 | 规则骨架 | `social-repository`、`social`、拓扑、传播 |
| 推荐和感知 | Social 骨架 | `social-recommendations`、`social-ranking`、`social-perception` |
| 测量 | 任一骨架 | `measurements`、`measurement-repository`、采样策略 |
| 记忆或大数据 | 任一骨架 | Extras 的 `memory`、`datasets` |

先确认 `check` 通过，再增加机制。每加一个插件，问它改变了哪条路径、写入哪些表、需要哪些前置数据。需要模型时，先用规则版本验证结构和测量，再接模型。

## 6. 补数据

数据分三层准备：

1. `world`：主体 id、属性、来源；每个主体只出现一次。
2. 初始化：成员、关系、配对、拓扑；用插件初始化器写入，不在外部脚本里直接改表。
3. 数据集：不随 run 变化的大批量记录；用 `datasets.prepare` 编码，在写事务里 `attach`。

数据要能支持问题。例如研究传播，至少要有可区分的主体、可见的关系或推荐来源、以及传播事件的记录。数据不足时，先缩小问题，不要用随机数填补没有依据的部分。

## 7. 小规模试跑

先用小规模规则 run 验证：

```sh
node .build/cli/dist/main.js check --profile <profile> --lock module-lock.json \
  --base .build --world world.json --json
node .build/cli/dist/main.js run start --profile <profile> --dir runs/pilot \
  --ticks 2 --lock module-lock.json --base .build --world world.json --detach --json
node .build/cli/dist/main.js run status --dir runs/pilot --json
node .build/cli/dist/main.js run result --dir runs/pilot --json
```

检查：

- `check.ok` 为 `true`，没有 `world` 或 `actor` 项；
- `run status` 到了预期 tick，`lastReason` 符合终止设计；
- `run result` 的 `activations.completed` 和 `failures` 能解释每次激活；
- 结果里的主体数量、关系数量、测量记录与设计一致；
- 重开同一 run 读取结果时，完整点和观察一致。

试跑通过后，再决定是否扩大规模或接模型。需要比较干预时，用[因果分支](causal-branches.md)从同一个完整点建立目标 run，而不是改原 run 的配置。

## 结果记录

一次实验至少保存：

- profile、lockfile、world 文件和数据集的 id；
- run 目录、lineage 目录和完整点 id；
- 实际命令和环境（不记录凭据值）；
- `run result` 的 `result.cut`、`activations` 和测量记录；
- 与设计不一致的地方，以及未完成或未知的操作。

只报告“跑通了”不足以支持结论。要说明结果是在哪个完整点、哪组主体、哪份数据上得到的。
