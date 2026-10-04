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

plain_plugin 提供空白基准；round_robin_plugin 管理配对、轮次消息与完整历史；social_plugin 提供网络、帖子、推荐、互动及曝光。多个同类插件使用不同名字，在同一环境显式绑定，主体身份共享。CodeSchedule 安排领域时序，插件 on_step 完整步骤钩子自动收束。

## Designing Realistic LLM Scenes

For LLM-based agents, design the env around constrained evidence:

1. List what the agent could plausibly observe at that moment.
2. Encode that as one or more FoVs.
3. List what the agent could plausibly do.
4. Expose those as actions, not prose-only instructions.
5. Decide what is recorded as state, logs, tables, or memories.
6. Keep hidden variables out of visible agent state.
7. Use `interview(...)` for measurement and `instruct(...)` for behavior.

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
