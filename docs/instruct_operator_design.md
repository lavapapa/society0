# 设计文档：增强 `instruct` 算子以支持 FoV 和记忆控制

**版本**: 1.0
**状态**: 提案

## 1. 前因后果 (Background & Context)

当前框架中的 `instruct` 算子（Operator）仅仅是一个占位符，功能非常有限。为了支持更复杂的、由 LLM 驱动的 Agent 行为，我们必须对其进行增强。

核心需求是，在向 Agent 发出指令（instruct）时，我们希望能够：

1.  **预先提供上下文**: 在 Agent 思考前，先调用一个环境的视野函数（FoV），并将结果作为背景信息提供给 Agent。
2.  **控制交互记忆**: 能够控制某次指令交互是否应被 Agent “遗忘”，以支持“访谈”这类不应影响 Agent 正常行为的特殊场景。

## 2. 设计目标 (Design Goals)

本次修改旨在达成以下目标：

1.  **实现 FoV 按需调用**: `instruct` 算子应能根据配置，可选地调用一个指定的 FoV 函数，并将结果注入到传递给 Agent 的上下文中。
2.  **实现记忆控制**: `instruct` 算子应能接受一个布尔参数 `is_memory`，并将其传递给 Agent 的执行方法。
3.  **保持接口简洁**: 这些新功能应作为 `instruct` 算子的内部逻辑实现，用户只需在配置文件中提供简单的参数即可使用。
4.  **最小化框架改动**: 遵循“方案一”的原则，将代码修改尽可能地局限在 `core_data.py` 和 `schedule.py` 中的 `_instruct_operator` 函数内部。

## 3. 方案设计与思路 (Proposed Design)

我们将采用“智能算子”方案，即保持核心调度引擎不变，将所有新逻辑封装在 `_instruct_operator` 内部。

### 3.1 修改 `core_data.py`

为了支持记忆控制，我们需要修改 `LLMAgent` 的核心接口。

*   **目标**: `LLMAgent.execute_instruction` 方法。
*   **修改**: 为该方法增加一个布尔类型的参数 `is_memory`，并设置其默认值为 `True`。
*   **新签名**: `execute_instruction(self, instruction: str, context: Dict[str, Any], is_memory: bool = True)`
*   **理由**: 这样，Agent 的内部逻辑就可以根据这个标志位，来决定是否要将本次交互存入自己的记忆库。默认 `True` 保证了向后兼容性，常规的仿真交互会被正常记忆。

### 3.2 修改 `schedule.py`

所有新功能的核心逻辑都将在这里实现。

*   **目标**: `_instruct_operator` 函数。
*   **修改**: 完全重写该函数的内部实现，使其能够处理新的配置参数。
*   **核心思路**: `_instruct_operator` 在执行时，会从传递给它的 `params` 字典中解析三个关键参数：
    1.  `instruction: str` (必需): 指令的核心内容。
    2.  `fov: str` (可选): 要调用的 FoV 函数的**名称**。
    3.  `is_memory: bool` (可选): 是否记忆，默认为 `True`。

*   **内部工作流**: 对于每一个被选中的 Agent，`_instruct_operator` 将执行以下步骤：
    1.  创建一个临时的 `llm_context` 字典，用于存放本次交互的所有背景信息。
    2.  检查 `params` 中是否存在 `fov` 键。如果存在：
        *   根据 `fov` 的值（函数名），从 `FunctionRegistry` 中查找到对应的 FoV 函数。
        *   为当前 Agent 调用该 FoV 函数。
        *   将 FoV 函数的返回值存入 `llm_context` 中，例如 `llm_context['fov_result'] = ...`。
    3.  从 `params` 中获取 `is_memory` 的值（若未提供则默认为 `True`）。
    4.  最后，调用 `agent.execute_instruction()`，并将 `instruction`、包含 FoV 结果的 `llm_context`、以及 `is_memory` 标志位一同传递过去。

### 3.3 配置文件示例 (Example Configuration)

完成修改后，用户可以在 `schedule` 配置文件中像这样使用新的 `instruct` 算子：

```yaml
# schedule.yaml
nodes:
  - id: interview_alice
    selector: { type: by_id, agent_ids: [alice] }
    operators:
      - type: instruct
        # 必需参数
        instruction: "你好，Alice，我们想对你进行一次关于市场看法的中立访谈。"
        
        # 可选参数：在提问前，先让 Alice 看到最新的市场概览
        fov: "get_market_overview"
        
        # 可选参数：本次访谈不应被 Alice 记忆
        is_memory: false
```

## 4. 优缺点 (Pros & Cons)

*   **优点**:
    *   **改动集中**: 所有核心逻辑都封装在一个函数内，不影响框架其他部分。
    *   **实现简单**: 无需修改 `StepNode` 等核心数据结构，开发和测试都相对直接。
*   **缺点**:
    *   **算子职责过重**: `_instruct_operator` 同时负责了数据获取（调用 FoV）和指令执行，功能不够单一。
    *   **扩展性受限**: “前置 FoV 调用”的能力被绑定在了 `instruct` 算子内部，不易被其他算子复用。

---

该方案是在“最小化改动”和“实现核心功能”之间取得的平衡，符合我们当前的决策。请审阅。
