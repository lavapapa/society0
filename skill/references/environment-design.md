# 环境设计

环境不是背景板。它决定主体看到什么、能做什么、行动会留下什么、哪些约束会被强制执行。先把环境写清，再决定主体的类型和数量。

## 三个核心问题

- **可见性**：谁在什么时点能看到哪些材料？材料是原文、摘要、排序后的列表，还是带来源标签的片段？
- **行动能力**：谁可以发布、回复、转发、关注、反应、发送私信、广播、更新状态？哪些行动需要前置条件？
- **环境约束**：推荐权重、可见窗口、内容长度、成员资格、轮次、权限、资源配额、惩罚和终止条件。约束是研究变量，不是中性管道。

## 当前公开插件能提供什么

### 基础世界（Core）

- `@society0/core/plain`：主体记录、`plain.update` 行动、`plain.actors` 信息源，适合规则基线、状态更新和最小实验。
- `@society0/core/actors`：从 world 的 `actors` 段写入主体；`seed: true` 时要求 `actors.seed` 初始化。
- `@society0/core/plain-init`：在 profile 里直接写初始主体，适合少量主体或入门示例。不要和 `actors.seed` 同时开启。
- `@society0/core/proposal-sequence`：按配置的 proposal 顺序推进 tick，适合规则驱动的固定协议。
- `@society0/core/proposal-events`、`@society0/core/runtime-events`、`@society0/core/clock-event`：由外部事件驱动，适合事件源和历史重放式研究。事件偏移必须递增，时间不能倒退。
- `@society0/core/clock-ticks`、`@society0/core/runtime-staged`：按 tick 推进的规则协议。
- `@society0/core/perception`、`@society0/core/perception-actor`：感知来源、感知读取和主体感知。
- `@society0/core/actions`：行动注册、发现、执行和回执。
- `@society0/core/public`、`@society0/core/public-self`、`@society0/core/moment-views`：公开数据、主体自视图和时点视图。
- `@society0/core/results`、`@society0/core/thread`：结果记录和 Thread。
- `@society0/core/random`：可寻址随机流，配置 `algorithm` 和 `seed`。
- `@society0/core/termination`、`@society0/core/checkpoint-policy`、`@society0/core/run-controller`：终止、完整点和推进控制。
- `@society0/extras/workspace`、`@society0/extras/artifacts-local`：工作区和产物存储。
- `@society0/extras/datasets`、`@society0/extras/dataset-jsonl`：不可变、分帧的数据集读取，适合较大的研究数据；小规模研究数据也可以放在公开表或初始化配置里。

### 社交世界（Social）

- `@society0/social/social-repository`：成员、关系、帖子、回复、反应、投递、通知、传播事件和呈现数据。
- `@society0/social/social-init`：在主体 seed 之后创建社交成员和关系；主体不存在时报 `ACTOR_MISSING`。
- `@society0/social/topology-edges`：显式边、随机、小世界、无标度、完全图或目标变异系数网络。随机拓扑使用 Core 的具名随机地址。
- `@society0/social/social`：注册社交行动和信息查询。行动包括 `social.publish`、`social.reply`、`social.repost`、`social.react`、`social.follow`、`social.unfollow`、`social.preferences`、`social.consumeNotifications`；配对配置开启后还有 `social.sendPartnerMessage`、`social.broadcastToGroup`。
- 信息源包括 `social/feed`、`social/profile`、`social/network`、`social/notifications`、`social/posts`、`social/trending`；配对配置开启后还有 `social/pairing`、`social/pairingPlan`、`social/conversation`、`social/conversationHistory`、`social/conversationMembers`。
- `@society0/social/propagation-neighbors`：邻居传播策略。
- `@society0/social/social-ranking`：排名注册和分数分解。
- `@society0/social/social-recommendations`：推荐准备、来源版本和呈现。
- `@society0/social/social-perception`：把资料、投递动态、通知和推荐注册为感知来源。`previewChars` 只截短展示正文；排名、测量和存储正文保留完整内容，原文可通过 `social/posts` 的 `{id}` 读取。
- `@society0/social/measurements`、`@society0/social/measurement-every`、`@society0/social/social-measurement`：测量登记、采样策略和排名比较。
- `@society0/social/round-robin`：按持久游标轮流激活主体，适合配对、访谈和固定轮次协议。

### LLM 主体（Social profile 常用）

- `@society0/core/model-providers`、`@society0/core/model-pi`、`@society0/core/model-credentials`：模型、提供方和凭据引用。
- `@society0/core/driver-llm`：完整行动循环、感知、预算、必需行动和完成判定。
- `@society0/core/actor-tools`、`@society0/core/agent-runtimes`、`@society0/core/agent-runtime-pi`：主体工具和运行时。
- `@society0/core/action-files`：`/self/actions/offers.jsonl` 与 `/self/actions/catalog.jsonl` 的完整行动清单。
- `@society0/core/mount-workspace`、`@society0/core/mount-results`、`@society0/core/mount-context`：工作区、结果和上下文挂载。

## 可见范围怎么设计

对每个主体、每个时点，列出：

```text
主体：
时点：
能看到的来源：
看到的是原文、摘要还是排序列表：
看不到什么：
为什么看不到：
```

设计检查：

- 主体看到的是情境中的证据，不是研究者的结论。
- 隐藏条件不出现在公开表、主体属性或提示里。
- 推荐、排序、截断和来源标签被当作实验条件记录。
- 重要原文能通过引用或 `social/posts` 的 `{id}` 回到完整内容。
- 展示截短不影响测量和排名使用的完整正文。

## 行动怎么设计

优先使用窄而明确的行动，不要给一个“对社会作出反应”的宽泛入口。每个行动写清：

```text
名称：
输入字段：
可用条件：
成功后的状态变化：
写入的回执或记录：
被拒绝时的业务原因：
执行故障与业务拒绝的区别：
```

行动插件要把“规则拒绝”和“执行故障”分开。规则拒绝表示环境按业务规则不允许，世界状态没有改变；执行故障表示插件异常、持久化失败、传输未知或平台错误。前者不应被当成样本失败，后者必须保留原始诊断。

## 记录与测量

环境至少留下三类记录：

- **行动回执**：谁在什么时点做了什么，成功、拒绝、等待还是未完成。
- **状态**：主体属性、关系、帖子、资源、权限、配额。
- **测量**：访谈、问卷、评分、排名比较、研究侧标注。

结果变量应能从这些记录重算。只有模型自述、没有行动回执或状态变化的行为，不能当成世界后果。

## 规模与顺序

- 串行阶段保持业务顺序；明确独立的阶段才使用并行容量。
- 主体调度容量、提供方请求上限和计算资源分开设置。
- 先让少量主体和少量 tick 的暴露、行动、记录都说得通，再加网络、记忆和规模。
- 传播、推荐、配对等机制的顺序会改变结果，必须在 ODD 里写清。
