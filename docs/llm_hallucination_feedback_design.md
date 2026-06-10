# 设计文档：通过增强动作反馈缓解LLM幻觉

**作者:** Gemini

**日期:** 2025年9月20日

**状态:** 提案

## 1. 问题描述

在仿真实验中，我们观察到LLM智能体（特别是`charlie`）会尝试执行一些针对不存在的实体（智能体或帖子）的动作，例如 `follow('expert_in_ai_finance')`。经过对代码的详细分析，我们确认这不是由于FoV（视野）信息不足或格式不清导致的，因为FoV已经明确提供了有效的实体ID，并警告不要编造ID。

问题的根源在于 **LLM的幻觉（Hallucination）**。在这种情况下，LLM的角色扮演（Persona）和任务目标（例如，“寻找并关注专家”）的驱动力，超过了它遵循提示中事实约束的能力，导致它“创造”出符合其逻辑但与世界状态不符的参数。

## 2. 解决方案：通过纠正性反馈进行在线学习

我们不打算通过修改核心框架来增加复杂的预执行验证层，而是采纳一种更轻量、更符合智能体学习范式的策略：**增强动作失败时的错误信息，为LLM提供明确的、可操作的纠正性反馈。**

这种方法利用了引擎现有的`动作 -> 工具调用结果 -> 下一轮提示`的循环，通过提高反馈信号的质量，让智能体在“试错”中“学习”其所在环境的规则。

## 3. 具体实施计划

此修改不涉及核心引擎架构的变动，仅需对 `SocialNetworkEnv` 中几个动作函数的错误返回信息进行文本上的优化。

- **文件:** `src/simengine/env/social_network/env.py`
- **类:** `SocialNetworkEnv`

### 3.1. 修改 `follow` 动作

**当前实现:**
```python
if target_agent_id not in context.world.agents_data:
    logger.warning(f"Agent {agent.id} 尝试关注不存在的Agent {target_agent_id}")
    return f"Agent {target_agent_id} not found"
```

**问题:** 返回的错误信息 `"Agent expert_in_ai_finance not found"` 对LLM来说信息量不足，它不知道“有效”的ID是什么样的。

**建议修改:**
```python
if target_agent_id not in context.world.agents_data:
    logger.warning(f"Agent {agent.id} 尝试关注不存在的Agent {target_agent_id}")
    # 获取当前可见的其他用户ID作为提示
    other_agents = self._get_other_agents_in_network(agent, context.world)
    valid_ids_hint = ", ".join(other_agents[:5]) # 最多显示5个作为示例
    error_message = (
        f"错误：关注失败。用户 '{target_agent_id}' 不存在。"
        f"请从你看到的信息中选择一个有效的用户ID，例如：{valid_ids_hint}。"
        f"不要编造用户ID。"
    )
    return error_message
```

**效果:** 新的错误信息不仅告诉LLM它错了，还告诉了它**为什么错**（用户不存在），并提供了**如何修正**的清晰指令和示例（从你看到的信息里选，例如...），强化了正确的行为模式。

### 3.2. 修改 `like_post` 动作

**当前实现:**
```python
if not post:
    logger.warning(f"Agent {agent.id} 尝试点赞不存在的帖子 {post_id}")
    return f"Post {post_id} not found"
```

**问题:** 与 `follow` 类似，`"Post post_ai_finance_opinion not found"` 这样的信息无法帮助LLM纠正行为。

**建议修改:**
```python
if not post:
    logger.warning(f"Agent {agent.id} 尝试点赞不存在的帖子 {post_id}")
    # 获取当前可见的帖子ID作为提示
    visible_posts = self._get_real_posts_only(agent, context.world)
    valid_ids = [p.get('post_id') for p in visible_posts]
    valid_ids_hint = ", ".join(valid_ids)
    error_message = (
        f"错误：点赞失败。帖子 '{post_id}' 不存在。"
        f"请从你的推荐动态中选择一个有效的帖子ID。当前可见的帖子ID包括：{valid_ids_hint}。"
    )
    return error_message
```

**效果:** 同样地，为LLM提供了上下文相关的、可操作的纠正指令，引导其在后续的决策中选择有效的帖子ID。

### 3.3. （可选）修改其他相关动作

可以对 `reply_to_post` 等其他需要实体ID的动作进行类似的修改，以增强整个系统的鲁棒性。

## 4. 结论

该方案是一个高度针对性、低成本、高回报的优化。它承认并接受了LLM会产生幻觉的现实，并巧妙地利用了引擎的内置反馈循环，将其从一个简单的“错误通知”机制，转变为一个对智能体的“在线教学”机制。这不仅能有效缓解当前遇到的幻觉问题，也提升了整个仿真平台在与LLM交互时的整体稳健性。
