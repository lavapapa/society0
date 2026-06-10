# 设计文档：LLM Agent 认知架构

**版本**: 2.0
**状态**: 最终方案

## 1. 核心设计原则

为实现一个可信、通用、可扩展的社会模拟智能体（Agent），其认知架构遵循以下核心原则：

1.  **模块化心智 (Modular Mind)**: Agent 的“心智”由多个独立的、可组合的模块（记忆、推理、人格等）构成，而非一个单一的黑箱。

2.  **Agent 即装配者 (Agent as Assembler)**: `LLMAgent` 类是顶层的“装配者”和“协调者”。它负责在每次决策时，从各个模块中获取信息，动态地组装成可供“推理引擎”使用的上下文。

3.  **主客观分离 (Subjective vs. Objective)**: 严格区分 Agent 的主观内在状态（它自己知道什么）和客观物理属性（世界知道它是什么），以实现更真实的模拟，并为系统提供必要的控制抓手。

4.  **能力与法则分离 (Ability vs. Law)**: 明确区分 Agent 的“能力”（`Skill`，它可以决定做什么）和世界的“法则”（`Behavior`，决定了事情如何发生），建立一个有规则的、非“唯心”的模拟世界。这借鉴了 `dog.bark()` 的隐喻，强调了直接行动的有效性。

## 2. `LLMAgent` 核心组件

一个 `LLMAgent` 实例由以下核心组件构成，并由 `LLMAgent` 类自身进行“装配”。

### a. `Persona` (人格)

*   **定义**: 一段**非结构化的、描述性的自然语言文本**。它如同角色小传，定义了 Agent 的背景故事、核心性格、价值观和行为准则。
*   **作用**: 作为 `system_prompt` 的主要内容，为 LLM 的行为和语言风格提供基础性的、长期稳定的指导。

### b. `state` (内在状态) 与 `properties` (外在属性)

`Agent` 的数据结构被明确地划分为两个部分：

*   `state: Dict`: 代表 Agent 的**主观、自我可知**的状态。这里存放着 Agent 的情绪、计划、短期目标等。这部分信息可供 Agent 的“心智”（LLM）进行推理。
*   `properties: Dict`: 一个**新增的顶层字段**，用于存储 Agent **客观、但对自身不可知**的物理属性。例如：精确的坐标、物理ID等。这部分信息对系统（如 `Selector`）可见，但**不会**被送入 LLM 的提示词中。

### c. `reminders` (提醒机制)

*   **定义**: `Agent` 数据结构上的一个**新增的顶层字段** (`reminders: List[str]`)，与 `state` 和 `properties` 并列。
*   **作用**: 用于接收由外部（如环境 `Behavior`）施加的状态变更的文本通知，确保 Agent 的“意识”与它的“身体”状态保持同步。
*   **行为**: 该字段需要被持久化。`LLMAgent` 在每次 `instruct` **之前**会检查此列表，将其中的提醒信息注入到 `user` prompt 的最前端，然后**清空**该列表（一次性消费）。

### d. 记忆系统 (Memory System) 与 推理引擎 (Reasoning Engine)

*   这两个是独立的、可插拔的服务模块，`LLMAgent` 负责调用它们。
*   **记忆系统**: 其详细设计见 `docs/memory_system_design.md`。
*   **推理引擎**: 即 `execute_tool_call_loop` 函数，其详细设计见 `docs/agent_loop_design.md`。

## 3. `instruct` 方法：核心协调器

`instruct` 是 `LLMAgent` 的核心入口方法，它不包含具体的业务逻辑，只负责协调和组装。

*   **新参数 `tool_tags`**: `instruct` 方法的签名中将包含一个可选参数 `tool_tags: List[str] | None = None`。
*   **作用**: 该参数允许外部调用者（如 `Operator`）动态地限制本次 `instruct` 调用中 Agent 可用的 `Skill` 范围。如果 `tool_tags` 被提供，`LLMAgent` 会在调用 `ToolCallLoop` 之前，根据 `tag` 从其完整的 `Skill` 集合中筛选出一个临时的子集。
*   **价值**: 这为研究者提供了强大的实验控制能力，可以研究 Agent 在能力受限下的行为变化。

## 4. `Behavior` 与 `Skill`：最终范式

这是我们对 Agent “能力”和“世界规则”的最终定义。

### a. `Behavior` (行为)

*   **取代** `rule`。
*   **定义**: 一个由**调度器 (`Schedule`)** 自动触发的函数。它代表世界的“物理规律”（如昼夜更替）或 Agent 的“条件反射”（如天黑了就感到疲倦）。
*   **输出**: 通过返回 `StatePatch` 列表来修改世界状态。

### b. `Skill` (技能)

*   **取代** `agent_tool`。
*   **定义**: 一个 Agent 可以在 `ToolCallLoop` 的 `Actions` 阶段**有意识地、主动决定调用**的能力。
*   **输出**: 它的实现逻辑也通过返回 `StatePatch` 列表来**直接描述**其行为对世界造成的后果。这确保了 Agent 直接行动（如 `publish_post`）的简洁性和高效性。
*   **合作模式**: 对于需要复杂计算或依赖世界规则的 `Skill`（例如 `work`），其内部实现可以从 `FunctionRegistry` 中查询并调用一个或多个 `Behavior` 函数来辅助计算，然后用计算结果来构建最终的 `StatePatch`。这实现了 `Skill` 对 `Behavior` 的调用与合作。

## 5. 总结：LLMAgent 单步完整工作流

1.  `LLMAgent.instruct` 被外部（如 `Operator`）调用，并可能传入 `tool_tags`。
2.  `LLMAgent` 从 `self.state.reminders` 中读取并清空提醒信息。
3.  `LLMAgent` 调用 `self.memory.retrieve()`，根据当前任务获取相关记忆。
4.  `LLMAgent` **组装** `system_prompt`（基于 `Persona` 和部分 `state`）和 `user_prompt`（基于 `reminders`, 记忆, 和原始 `instruction`）。
5.  `LLMAgent` 根据 `tool_tags` 从完整的 `Skill` 集合中**筛选**出本次调用可用的 `ToolSet`。
6.  `LLMAgent` 调用**推理引擎** `execute_tool_call_loop()`，并传入组装好的提示词和筛选后的 `ToolSet`。
7.  `ToolCallLoop` 返回包含 `phases` 的结果。
8.  `LLMAgent` 解析结果，并可选择性地触发**情绪模型**更新自身情绪，或生成总结并调用 `self.memory.add_episodic_memory()` 写入新记忆。
