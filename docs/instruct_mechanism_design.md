# 设计文档：高级 `instruct` 交互机制

**版本**: 1.0
**状态**: 最终提案

## 1. 背景与问题 (Background & Problem Statement)

在我们的认知架构中，`LLMAgent` 通过 `instruct` 方法来驱动其核心的“思考-行动”循环。然而，一个简单的 `instruct` 实现面临两大挑战，导致其无法满足复杂、可控的仿真需求：

1.  **上下文注入难题**: Agent 在决策时，往往需要一些“即时”的、动态的外部信息（例如，“当前的市场价格”、“社交圈的最新动态”）。如果将获取这些信息的逻辑硬编码在 Agent 内部，会导致 Agent 与环境的紧密耦合。而如果完全依赖 `schedule` 的数据流，又会使 `schedule` 的配置变得异常复杂和笨重。

2.  **输出结果的不可靠性**: LLM 的输出本质上是自然语言文本，即使通过提示词引导，也无法保证其始终遵循我们期望的格式。依赖脆弱的正则表达式或字符串解析来提取结构化数据，会使系统变得非常不稳定，无法满足科学模拟对数据精确性的要求。

为解决这两个核心问题，我们设计了以下两套相互协作的机制。

## 2. 设计原则

*   **职责分离 (Separation of Concerns)**: 严格划分调度器 (`Schedule`)、协调者 (`LLMAgent`) 和推理引擎 (`ToolCallLoop`) 的职责。调度器负责“驱动”，协调者负责“组装”，推理引擎负责“思考”。
*   **可靠性优先 (Reliability First)**: Agent 的输出应尽可能地结构化、可验证，最大程度地减少对自然语言解析的依赖。
*   **渐进增强 (Progressive Enhancement)**: 新功能应作为可选项加入，在不增加基础用法复杂度的前提下，提供更强大的高级功能。

---

## 3. 方案一：调度器驱动的 FoV 传递机制

此方案旨在解决“上下文注入”的难题。

### a. 设计详述

我们将 `instruct` 算子（Operator）的功能，从一个简单的指令文本，扩展为一个可以携带“数据采集请求”的指令包。其核心思想是：**由调度器负责采集数据，Agent 只负责消费数据。**

**工作流**:
1.  **配置**: 研究者在 `schedule.yaml` 的 `instruct` 算子参数中，增加一个可选的 `fovs: List[str]` 列表，其中包含一个或多个 FoV 函数的名称。
2.  **执行**: `schedule.py` 中的 `_instruct_operator` 在执行时，会检查这个 `fovs` 列表。
3.  **采集**: 对于每个需要执行指令的 Agent，`_instruct_operator` 会遍历 `fovs` 列表，并为该 Agent 调用在 `FunctionRegistry` 中注册的、同名的 FoV 函数（例如 `env.get_recommended_feed(agent, ...)`）。
4.  **注入**: `_instruct_operator` 将所有 FoV 的返回结果收集到一个字典中（例如 `{"get_recommended_feed": [...]}`），然后通过 `instruct` 方法的 `context` 参数，将其**一次性地注入**给 `LLMAgent`。
5.  **消费**: `LLMAgent` 在组装最终的 `user` prompt 时，只需从 `context` 参数中取出已经准备好的 FoV 结果，并将其格式化为自然语言即可。

### b. 协作与依赖

*   此设计下，`_instruct_operator` 成为了一个轻量级的“数据采集与分发”中心。
*   它要求 `LLMAgent.instruct` 方法必须能接收一个通用的 `context: Dict` 参数。
*   **破坏性分析**: 这是一个**非破坏性增强**。对于不需要 FoV 的简单 `instruct` 调用，用户无需添加 `fovs` 字段，原有行为不受影响。

---

## 4. 方案二：基于动态 `Action` 的强制结构化输出

此方案旨在解决“输出结果不可靠”的难题。

### a. 设计详述

核心思想是：**利用大模型本身最擅长的能力（Function Calling），来规范它自己的输出。** 我们将“提交最终答案”这个动作，本身也变成一个需要被调用的 `Action` (即 `Skill`)。

**工作流**:
1.  **定义 Schema**: `LLMAgent.instruct` 方法接受一个可选的新参数 `output_schema: Optional[Dict]`。这个 `schema` 是一个标准的 OpenAI Function Calling JSON Schema，描述了期望的输出数据结构。
2.  **动态创建**: 如果 `output_schema` 被提供，`instruct` 方法会在内部**动态创建一个**名为 `finish_instruction` 的临时 `Action`，其参数定义就是 `output_schema`。
3.  **注入与提示**: 这个动态创建的 `Action` 会被加入到传递给 `ToolCallLoop` 的 `ActionSet` 中。同时，系统提示词中会增加一句：“**任务完成时，你必须调用 `finish_instruction` 来提交你的最终结构化结果。**”
4.  **验证与强制**: `ToolCallLoop` 结束后，`instruct` 方法会检查 `finish_instruction` 是否被调用。
    *   **如果未调用**，则启动一个“强制执行轮次”，向 `messages` 历史中追加一条 `user` 消息：“请调用 `finish_instruction` 来完成任务。”，并再次调用 运行loop
    *   **如果已调用**，则任务成功。
5.  **提取结果**: `finish_instruction` 被调用时传入的参数，将被提取出来，作为最终的 `structured_output`。

### b. 协作、依赖与限制

*   **协作**: 该机制需要 `LLMAgent.instruct`（负责动态创建）和 `execute_action_loop`（负责执行）的紧密配合。
*   **技术依赖**: 此方案高度依赖底层 LLM 模型的 Function Calling/Tool Use 能力。如果更换的模型不支持此功能，该机制将失效。
*   **限制**: “强制执行轮次”会增加额外的 LLM 调用开销，但这是为了保证可靠性所必需的成本。
*   前提：这个schema可以在 `schedule.yaml` 的 `instruct` 算子参数中 进行定义。

### c. 可选性与破坏性

*   这是一个**完全可选的、非破坏性的渐进增强**。如果调用 `instruct` 时不提供 `output_schema` 参数，则整个机制不会被激活，`instruct` 的返回值中 `structured_output` 字段将为 `None`。

## 5. 最终产物：`InstructOperatorResult`

`_instruct_operator` 的最终返回值将是一个结构化的对象，以容纳所有这些信息：

```python
@dataclass
class InstructOperatorResult:
    agent_id: str
    status: str # "success", "error"
    performative_output: str # 从 phases 中解析出的、对外表现的文本
    structured_output: Optional[Dict] # 从“输出Action”中解析出的结构化数据
    phases: Dict # 完整的思考阶段记录
    total_turns: int # 推理循环的总轮次
```
