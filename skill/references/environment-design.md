# 环境设计

所有主体共享一个环境，插件实现其中的机制。

## Why Environment Comes First

In Society0, an environment is not scenery. It is the experiment's social situation:

- what agents can see through FoVs.
- what agents can do through actions.
- what records interactions leave behind.
- what constraints, institutions, platform rules, or physical/social context shape behavior.
- what state changes the simulation can measure over time.

Agents are participants inside the environment. A good experiment usually starts by defining the environment, then asks what agent types belong there. For LLM-based simulations, this matters because the environment supplies realism boundaries: visible evidence, allowed behavior, social affordances, and reminders that prevent the model from inventing context.

Use three concepts to design the environment:

- **FoVs**: the environment acts as a visibility machine. It decides what each agent sees, hears, reads, or receives as social evidence.
- **Empower**: the environment grants action possibilities. It decides who may post, reply, move, moderate, buy, vote, recommend, or stay read-only.
- **Hosting**: the environment hosts and constrains state that belongs to the situation, such as location, mute status, role permissions, resource access, exposure counters, or platform penalties.

These are not only implementation details. They are research variables. Recommendation rules, visibility windows, action permissions, and hosted constraints are often the actual mechanism being studied.

## 实现机制

Plugin 声明本机制 schema、同步 initialize 或可选异步 prepare、安装服务与显式依赖。权威 SQL 记录和不可变正文由规范写入器同步维护投影；小状态可以直接用明确表，派生图/数组作为可重建缓存。需要非 JSON 外源的初始化先准备资源，在根发布后释放；例子见 graph_environment。

Information 定义主体可读的文档与数据集，Actions 注册按对象类型发现的模板；发现、读取、调用权限独立，执行重新校验当前条件。一次领域写入有明确事务边界，跨介质部分失败使整个步骤失效。信息文档保原文、授权与版本，巨大列通过正文引用范围读取。

plain_plugin 提供空白基准；round_robin_plugin 管理配对、轮次消息与完整历史；social_plugin 组合独立领域、推荐和呈现叶服务。Information 预览保持只读，实际模型呈现通过 social_cognition_plugin 的 input_builder 接线；排序替换、语义开关与呈现效果见 [社交合同](../../docs/core-next/social-contract.md)。[推荐观测例子](../../examples/core_next/recommendation_observer.py) 展示旁路消费者复用推荐输入和多个策略并保存自己的结果。多个同类插件使用不同名字，在同一环境显式绑定，主体身份共享。Schedule 安排领域时序，插件 on_step 完整步骤钩子自动收束。

### 从双机制例子构建自己的世界

[conversation_pilot.py](../../examples/core_next/conversation_pilot.py) 中，`a/b/c/d` 是共享的主体，`work` 与 `commons` 是同一环境的两个机制实例。两者各自维护配对与消息，Actor 身份在两个机制间一致。研究者的 `schedule` 先通过 `pair` 阶段调用两个机制的 `start_round`，再通过 `talk` 阶段激活主体；主体的 `talk(session)` 调用各机制的发送行动，StepResult 保存实际配对与消息。

改造成自己的世界时，先把领域事实放入所属机制的表和规范写入器，再把允许主体读取的内容注册到 Information，把能够改变事实的行为注册到 Actions。插件作者通过共享 SQL 事务维护事实和投影，跨机制变更通过约定的服务或事务内写入函数组合；服务名称空间表示接口归属，领域维护责任由机制实现承担。主体私有资料、共享事实和研究者测量分别保留在各自接口中。

`includes` 静态展开子插件，`requires` 保证服务安装与资源退出的顺序；初始化在统一 schema 建立后按 `schema_requires` 的数据图顺序进行。步骤 before/after 钩子也按依赖安装顺序登记，因此依赖调整可能改变钩子顺序。配对后才能发送、交付后才能计税等研究因果次序，应像例子的 `pair → talk` 一样写成显式 Phase。钩子和资源关系见 [插件合同](../../docs/core-next/plugin-contract.md)。

同一 Moment 表示同一个业务时点，各次读取仍可取得最新 live 状态：串行阶段中 Bob 可以看到 Alice 刚刚产生的事实。若研究要求全部主体依据同一份开场材料决定，可在 `Phase.prepare` 取得一次不可变材料，并让主体使用 `session.prepared`；其他 Information 查询仍遵守其自身版本合同。将机制标为 independent 是作者对行动顺序无关性的声明，服务安装完成本身不作这种判断。

文件式交互延续同一行动规则。`/world` 呈现主体有权取得的共享材料，`/workspace` 保存主体自己的草稿和分析文件；购买订单需要调用购买 action，保存一份购买笔记只改变私有文件。模型应依照 action 回执区分受理、完成和拒绝，恢复身份在完整步骤发布后形成。可沿 [共享环境例子](../../examples/core_next/shared_environment.py) 的“查询订单 → 私有计算 → 购买行动 → 另一主体读通知”查看这条链，其 shell 依赖与调用方式见 [Shell 合同](../../docs/core-next/shell-contract.md)。

## Designing Realistic LLM Scenes

For LLM-based agents, design the env around constrained evidence:

1. List what the agent could plausibly observe at that moment.
2. Encode that as one or more FoVs.
3. List what the agent could plausibly do.
4. Expose those as actions, not prose-only instructions.
5. Decide what is recorded as state, logs, tables, or memories.
6. Keep hidden variables out of visible agent state.
7. Use `LLMPolicy(mode="interview")` for measurement and `LLMPolicy(mode="decision")` for behavior.

Example design move:

```text
Research phenomenon: friend endorsement changes credibility.
Environment: social_network.
FoV: recommended_feed includes post text, author, friend endorsements, and engagement counts.
Actions: like/comment/repost/follow.
Measurement: interview trust_score after exposure, with no ordinary actions.
Hidden condition: treatment/control stored in step params or researcher table, not agent state.
```

FoVs can be plain text or structured values. In current use, they should be designed as evidence that can be rendered into prompt text: feed items, notifications, partner messages, local observations, public rules, or institutional signals. Avoid giving the agent a global explanation of the experiment when a situated view is enough.

Actions should represent real affordances, not vague intentions. Prefer `publish_post`, `comment`, `follow`, `send_message`, `vote`, or `apply_for_job` over a broad "respond to society" action. The narrower the action, the easier it is to audit what happened.

Hosting is useful when the social situation should enforce constraints directly. Examples: a platform marks an agent as muted, a city env updates a resident's district, an organization env limits who can approve a decision, or a market env controls remaining inventory.
