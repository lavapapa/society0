# 虚假信息研究仿真平台增强设计文档

**作者:** Gemini

**日期:** 2025年9月20日

**状态:** 提案

**摘要:** 本文档旨在响应新的、更复杂的实验设计需求，提出对 `simengine` 核心框架的最小化、通用性修改，并详细阐述如何利用这些新功能来配置和实现一个结构严谨的“虚假信息传播与干预”研究。文档分为两部分：核心引擎的功能调整和具体实验的设计方案。

---

## 第一部分：核心引擎调整 (Core Engine Adjustments)

为了支持更广泛、更复杂的研究，同时保持外部研究代码的简洁性，我们建议对核心引擎进行以下三项通用化增强。

### 1. 引入专用的 `interview` (访谈) 操作符

**动机:** 当前使用通用的 `instruct` 操作符进行问卷调查，存在数据污染风险（智能体在“答题”时仍可执行社交动作，且“答题”行为本身会被记忆）。我们需要一个机制来保证测量过程的纯净性。

**设计方案:**

1.  **新增 `save_memory` 参数:**
    *   **文件:** `src/simengine/agent/core.py` (`LLMAgent.instruct` 方法)
    *   **修改:** 将现有 `is_memory` 参数重命名为 `retrieve_memory`，并新增 `save_memory` 参数，分别独立控制记忆的读取和写入。
    *   **签名变更:** `async def instruct(..., retrieve_memory: bool = True, save_memory: bool = True, ...)`
    *   **逻辑:** 方法内的记忆检索部分由 `retrieve_memory` 控制，记忆存储部分由 `save_memory` 控制。

2.  **创建 `interview` 调用链:**
    *   **`LLMAgent` 类 (`agent/core.py`):** 新增 `async def interview(...)` 方法，其内部实现与修改后的 `instruct` 方法几乎完全相同，旨在最大化复用认知循环代码。
    *   **`World` 类 (`core_data.py`):** 新增 `async def interview_agent(...)` 方法。此方法是安全保证的核心，它将调用 `agent.interview`，并**硬编码**以下参数：
        *   `retrieve_memory=True` (允许智能体回忆过去以回答问题)
        *   `save_memory=False` (禁止将访谈过程存入记忆)
        *   `action_tags=[]` (禁止执行任何需要标签的动作)
    *   **`Schedule` 编译器 (`schedule.py`):** 在 `_compile_operator` 方法中新增对 `type: interview` 的支持，使其编译后调用 `world.interview_agent`。

**最终效果:** 研究者可以在 `schedule.yaml` 中安全地使用 `type: interview`，引擎从架构层面保证了访谈过程的纯净性，无需研究者进行额外的手动配置。

### 2. 修正并启用环境规则 (Environment Rule) 操作符

**动机:** 研究需要在仿真过程中对环境（例如，所有帖子）执行一个全局操作（例如，给帖子打标签）。当前框架中，环境规则的注册 (`env_empowers`) 和调用 (`operators`) 存在脱节。

**设计方案:**

1.  **统一注册路径:**
    *   **文件:** `src/simengine/sim_engine.py` (`_register_environment_functions` 方法)
    *   **修改:** 当通过 `@rule` 装饰器发现一个环境方法时，不再将其注册到废弃的 `registry.env_empowers`，而是直接注册到 `registry.operators` 字典中。
    *   **代码示例:** `registry.operators[name] = { 'function': method, ... }`

2.  **明确使用方式:**
    *   此修改统一了所有“自定义”操作的调用方式。无论是外部注册的函数，还是环境类中定义的规则，都可以通过 `type: custom` 和 `function: <rule_name>` 在 `schedule.yaml` 中调用。

**最终效果:** 研究者可以方便地在自己的 `Environment` 子类中定义全局规则，并通过 `schedule.yaml` 在特定时间点（例如，选择器为 `environment` 的节点）调用它们，实现了对环境的顶层干预能力。

### 3. 帖子浏览量统计 (Post View Count)

**动机:** 帖子的影响力是社交网络仿真的一个关键指标，其最基础的代理变量就是浏览量。这个功能非常通用，应由框架提供支持。

**设计方案:**

1.  **修改数据模型:**
    *   **文件:** `src/simengine/env/social_network/models.py` (或 `Post` 类的定义处)
    *   **修改:** 为 `Post` 数据类增加一个新字段 `view_count: int = 0`。

2.  **在FoV函数中实现计数:**
    *   **文件:** `src/simengine/env/social_network/env.py` (`SocialNetworkEnv` 类)
    *   **修改:** 在 `get_recommended_feed` 方法中，每当一个帖子被挑选出来、即将放入推荐流返回给用户时，必须通过 `world` 代理对象递增其计数值：`world.environment_data.state['posts'][post_id]['view_count'] += 1`。

**最终效果:** 引擎自动、原子地记录每个帖子的浏览次数。研究者无需在自己的研究代码中实现此逻辑，可以直接从 `environment_data` 中读取和分析这些数据。

---

## 第二部分：虚假信息实验设计方案 (Misinformation Study Design)

本部分描述如何应用上述核心功能以及研究的特定配置来实现实验。

### 1. 实验配置 (`config.yaml`)

1.  **智能体:**
    *   **普通智能体:** 创建40个 `social_user` 类型的智能体，`archetype` 为 `llm`。我将为这40个智能体提供多样化的 `persona` 字符串，以模拟不同背景和观点的社群。
    *   **污染源智能体:** 创建1个ID为 `source_of_lies` 的智能体，其 `persona` 将被设定为一个偏执、坚定的虚假信息传播者。

2.  **环境:**
    *   `type: social_network`，并确保其 `Post` 模型已按第一部分的设计进行了修改。

### 2. 实验流程 (`schedule.yaml`)

每个仿真步骤将包含以下顺序执行的节点：

1.  **`agent_interaction_phase` (自由互动):**
    *   `selector`: `all_agents`
    *   `operator`: `type: instruct`, `action_tags: ["social", "memory"]`
    *   *目的:* 让所有智能体（包括污染源）根据其视野和记忆进行发帖、评论等社交活动。

2.  **`misinformation_posting_phase` (虚假信息注入):**
    *   `selector`: `type: by_id`, `agent_ids: ["source_of_lies"]`
    *   `operator`: `type: behavior`, `name: "post_misinformation_if_ready"`
    *   *目的:* 仅当满足特定条件时（见3.3），让污染源智能体发布带特定hashtag的虚假信息。

3.  **`intervention_phase` (干预实施):**
    *   `selector`: `type: environment`
    *   `operator`:
        *   `type: custom`
        *   `function: apply_intervention_tags` (在 `SocialNetworkEnv` 中定义的规则)
        *   `params:`
            *   `hashtag: "#illegal_immigrants_hunt_pets"`
            *   `intervention_rate: 0.5`
            *   `tag_to_apply: "此消息被AI模型标记为潜在虚假信息，请谨慎对待。"`
    *   *目的:* 对网络中符合条件的帖子实施干预（添加警告标签）。

4.  **`measurement_phase` (问卷与测量):**
    *   `selector`: `all_agents`
    *   `operators`:
        1.  **`interview_op`:**
            *   `type: interview`
            *   `question:` (包含 `questionnaire.md` 中所有问题的长文本)
            *   `output_schema:` (一个包含所有问题答案字段的JSON schema)
        2.  **`calculation_op`:**
            *   `type: behavior`, `name: "calculate_and_save_trust"`
            *   `input_mapping: { "survey_results": "interview_op.structured_output" }`
    *   **`converter`:**
        *   `type: jmespath`
        *   `expression:` (用于从所有智能体的 `properties.digital_trust` 中计算各维度平均分的JMESPath表达式)
    *   *目的:* 使用“纯净”的访谈方式收集数据，处理数据，并聚合成该step的最终因变量指标。

### 3. 自定义代码 (`behaviors.py` 和 `SocialNetworkEnv`)

1.  **`post_misinformation_if_ready` (Behavior):**
    *   **逻辑:** `if world.step >= 3: await agent.instruct(...)`，其指令是发布包含特定hashtag的帖子。

2.  **`calculate_and_save_trust` (Behavior):**
    *   **逻辑:** 接收 `survey_results` 字典，计算其中每个维度的平均分，并将结果（如 `avg_tech_trust`）写入 `agent.properties['digital_trust']`。

3.  **`apply_intervention_tags` (Environment Rule):**
    *   **定义于:** `SocialNetworkEnv` 类，并使用 `@rule` 装饰器。
    *   **逻辑:** 接收 `world` 和 `params`。遍历所有帖子，找到内容包含 `params['hashtag']` 的帖子。对其中 `params['intervention_rate']` 比例的帖子，检查其 `special_tags` 列表，若 `params['tag_to_apply']` 不在其中，则添加。

4.  **`get_recommended_feed` (Environment FoV):**
    *   **修改:** 在格式化帖子文本时，检查 `post.special_tags`。如果列表非空，则将列表中的所有tag（即警告文字）拼接到帖子内容的末尾，确保用户可以看到。
