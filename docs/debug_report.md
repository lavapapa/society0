# SimEngine 实验运行调试报告 (nohup.out)

**日期:** 2025-09-18
**分析基于:** `nohup.out` 日志文件

## 总体评估

本次实验运行暴露了3个主要问题，严重程度从高到低排列：

1.  **致命错误：JMESPath 类型错误导致测量节点崩溃**。这是最严重的问题，导致 `measurement_phase` 节点在每个步骤都执行失败。
2.  **逻辑错误：Agent 与不存在的帖子互动**。Agent 的感知（FoV）与环境的真实状态不一致，导致了大量无效的交互尝试。
3.  **效率问题：`finish_instruction` 未被首次调用**。认知循环需要一个额外的“强制执行轮次”来获取结构化输出，增加了LLM调用成本和延迟。

以下是每个问题的详细分析。

---

## 问题 1: (致命) JMESPath 在计算平均信任度时发生类型错误

### 1.1. 问题摘要

在 `measurement_phase` 节点的 `jmespath_converter` 中，尝试计算所有 agent 的平均信任分数时，程序因 `jmespath.exceptions.JMESPathTypeError` 而崩溃。错误表明 `avg()` 函数收到了一个包含 `None` 值的列表，而它期望的是一个数字数组。

### 1.2. 日志证据

```log
Traceback (most recent call last):
  File "/Users/marvin/Developer/projects/society0_v2/libs/simengine/src/simengine/schedule.py", line 1351, in _jmespath_converter
    transformed_data = jmespath.search(expression, jmespath_data)
...
jmespath.exceptions.JMESPathTypeError: In function avg(), invalid type for value: None, expected one of: ['array-number'], received: "null"

❌ [CONVERTER DEBUG] JMESPath converter error: In function avg(), invalid type for value: None, expected one of: ['array-number'], received: "null"
```

同时，转换器执行前的调试信息也给出了关键线索：

```log
🔄 [CONVERTER DEBUG] Sample agent alice structure:
🔄 [CONVERTER DEBUG] - properties keys: []
```

这明确表示，在 `jmespath_converter` 运行时，`alice` 的 `properties` 字典是空的。

### 1.3. 根本原因分析

错误的直接原因是 JMESPath 表达式 `world.agents_data.*.properties.digital_trust.tech` 在执行时，至少有一个 agent 的数据中不存在 `properties.digital_trust.tech` 这个路径。因此，查询结果是一个包含 `None` 的列表（例如 `[5, 4, None]`），`avg()` 函数无法处理 `None` 值，从而抛出类型错误。

深层原因是 **调度逻辑存在缺陷**。当前的 `schedule.yaml` 设计很可能如下：
1.  `agent_interaction_phase` 节点执行社交活动。
2.  `measurement_phase` 节点依赖于前者，并尝试在 **同一个节点内** 完成两件事：
    a.  通过 `instruct` 操作器，让 LLM Agent **生成** 它们的信任度分数。
    b.  通过 `jmespath_converter`，**立即读取并计算** 这些分数的平均值。

问题在于，`instruct` 操作器执行后，LLM 返回的信任分数（通过 `finish_instruction` action）存在于临时的 `operator_results` 中，但 **没有任何机制将这些分数写回到 `agent.properties` 的持久化状态里**。`jmespath_converter` 在查询 `world.agents_data` 时，发现 `digital_trust` 属性从未被设置，因此查询失败。

`studies/misinformation_study/behaviors.py` 中定义的 `update_digital_trust` 行为就是用来完成这个写入操作的，但它在当前的调度逻辑中没有被调用。

### 1.4. 问题定位

*   **错误触发点:** `src/simengine/schedule.py` 中的 `_jmespath_converter` 方法。
*   **逻辑设计缺陷:** `studies/misinformation_study/schedule.yaml` 的 `measurement_phase` 节点设计。
*   **缺失的环节:** `update_digital_trust` 行为没有被正确地编排在调度中。

### 1.5. 解决方案与建议

修改 `schedule.yaml`，将信任度的“生成”和“写入”与“测量”分离成两个独立的、有依赖关系的节点。

**建议的调度流程:**

1.  **`generation_phase` (新)**:
    *   **Operator (`instruct`)**: 指示 Agent 生成它们的信任度分数，并使用 `output_schema` 确保结构化输出。
    *   **Converter**: 一个简单的 `passthrough_converter`，将 `instruct` 的结果（包含 agent ID 和信任分数）传递到 `step_context`。

2.  **`writing_phase` (新)**:
    *   **Dependencies**: `['generation_phase']`
    *   **Selector**: 选择所有 Agent。
    *   **Operator (`behavior`)**: 调用 `update_digital_trust` 行为。
    *   **Input Mapping**: 将 `generation_phase` 节点的输出作为输入，把每个 Agent 的信任分数传递给 `update_digital_trust` 行为的 `trust_data` 参数。

3.  **`measurement_phase` (修改后)**:
    *   **Dependencies**: `['writing_phase']`
    *   **Operator**: 无（或者可以移除）。
    *   **Converter (`jmespath`)**: 保留现有的 JMESPath 表达式。此时，由于 `writing_phase` 已经将数据写入状态，查询将成功执行。

---

## 问题 2: (逻辑错误) Agent 尝试与不存在的帖子互动

### 2.1. 问题摘要

Agent（特别是 `alice`）多次尝试点赞（`like_post`）ID 为 `post_001`, `post_002` 的帖子，但均收到 `Post not found` 的错误。

### 2.2. 日志证据

```log
Agent alice 尝试点赞不存在的帖子 post_002
...
🔧 Executing Action: like_post
   Parameters: {'post_id': 'post_002'}
✅ Action Result: Post post_002 not found
```

### 2.3. 根本原因分析

这是 Agent 的 **感知与世界真实状态不一致** 导致的。
1.  在 `agent_interaction_phase` 节点，Agent 的 `instruct` 操作器调用了 `get_recommended_feed` 作为其视野（FoV）。
2.  在 `src/simengine/env/social_network/env.py` 的 `_get_sample_posts_for_demo` 方法中，当真实的环境状态 `self.state["posts"]` 为空时，它会生成一个 **临时的、用于演示的帖子列表**（包含 `demo_post_1`, `demo_post_2` 等）。
3.  LLM Agent 看到了这些演示帖子，并基于这些信息做出了“点赞”或“回复”的决策。
4.  然而，当 `like_post` Action 被执行时，它会检查 **真实的环境状态** `self.state["posts"]`。由于演示帖子从未被写入真实状态，查询自然会失败。

简而言之，用于演示的 mock 数据泄漏到了 Agent 的认知决策链中，导致了无效的行为。

### 2.4. 问题定位

*   **根源:** `src/simengine/env/social_network/env.py` 中的 `_get_sample_posts_for_demo` 方法。

### 2.5. 解决方案与建议

1.  **修复**: 在 `_get_sample_posts_for_demo` 方法中，不应该生成示例帖子！
3.  **改进 FoV**: FoV 函数应严格地只返回真实世界状态的视图。如果没有帖子，就“暂无帖子”就行了。

---

## 问题 3: (效率问题) `finish_instruction` 未被首次调用

### 3.1. 问题摘要

在 `measurement_phase` 节点中，当 `instruct` 操作器要求 Agent 提供结构化的信任度评估时，日志显示所有 Agent 都触发了 `"finish_instruction not called, starting enforcement round"` 的警告。

### 3.2. 日志证据

```log
Agent charlie: finish_instruction not called, starting enforcement round
Agent bob: finish_instruction not called, starting enforcement round
Agent alice: finish_instruction not called, starting enforcement round
```

### 3.3. 根本原因分析

`LLMAgent.instruct` 方法中有一个健壮性设计：当一个 `output_schema` 被提供时，它会动态地创建一个名为 `finish_instruction` 的 Action，并期望 LLM 在完成任务时调用它来提交结构化的结果。

当前的日志表明，LLM 在第一轮的响应中，没有按预期调用 `finish_instruction`。这触发了 `instruct` 方法中的 fallback 逻辑：向 LLM 发送一个额外的、强制性的提示，要求它必须调用 `finish_instruction`。

这虽然保证了最终能拿到数据，但存在以下问题：
*   **效率低下**: 完成一次 `instruct` 需要两次 LLM API 调用，增加了延迟和成本。
*   **提示词工程问题**: 这说明 `instruct` 操作器中的原始指令，或者 `LLMAgent` 的系统提示词，对于强制 LLM 使用 `finish_instruction` Action 的约束力不够强。

### 3.4. 问题定位

*   **核心逻辑:** `src/simengine/agent/core.py` 中的 `LLMAgent.instruct` 方法的强制执行轮次逻辑。
*   **提示词:** `measurement_phase` 节点中 `instruct` 操作器的 `instruction` 参数，以及 `LLMAgent._build_system_prompt` 中的系统提示词。

### 3.5. 解决方案与建议

1.  **强化系统提示词**: 在 `LLMAgent._build_system_prompt` 中，可以增加一句通用的、关于使用 Action 的强力约束（仅在确实需要结构化输出的时候再增加），例如：“最终你必须调用指定的工具来提交结果。”
3.  **检查 `act_prompt`**: 检查 `agent_loop.py` 中的 `DEFAULT_AGENT_ACT_PROMPT`，确保它清晰地说明了如何以及何时调用 Actions。

通过改进提示词，目标是让 LLM 在第一轮就能理解并遵守规则，从而避免进入效率较低的“强制执行轮次”。
