# 设计文档：可配置的多阶段 ToolCallLoop 引擎

**版本**: 2.0
**状态**: 最终提案

## 1. 前因后果 (Background & Context)

在 V1.0 的设计中，我们构思了一个基于“内在状态-动作-表现”三段式思考链的 `ToolCallLoop`。然而，经过进一步的讨论，我们认识到该设计虽然可行，但仍存在两个可以改进的关键点：

1.  **硬编码的认知阶段**: “三段式”的思考模式被固化在框架中，不够灵活，无法适应未来可能出现的、更多样化的 Agent 认知模型。
2.  **脆弱的解析标记**: 依赖 XML 风格的闭合标签（如 `<tag>...</tag>`）来解析 LLM 的输出，对模型的输出格式要求过高，一旦模型未能正确闭合标签，解析就会失败。

为了构建一个更健壮、更通用的 Agent 核心，我们提出了 V2.0 的设计。

## 2. 设计目标 (Design Goals)

1.  **通用阶段引擎**: `ToolCallLoop` 应成为一个通用的“多阶段思考”引擎，支持调用者传入任意的、有序的阶段列表（`stages: List[str]`）。
2.  **健壮的阶段标记**: 采用一种更不容易出错的、基于“起始标记”的系统来划分 LLM 输出的各个阶段。
3.  **灵活的输出结构**: 返回的结果中，每个阶段的输出格式应能根据其内容动态调整（纯文本为 `str`，包含工具调用则为 `list`）。
4.  **安全性**: 循环必须包含一个可配置的 `max_turns` 参数，以防止无限循环。
5.  **无状态与解耦**: 保持函数本身的无状态特性，将 LLM 的具体调用能力通过 `llm_call` 可调用对象注入。

## 3. 方案设计与思路 (Proposed Design)

### 3.1 函数签名 (Function Signature)

```python
def execute_tool_call_loop(
    instruction: str,
    tool_set: ToolSet,
    system_prompt: str,
    stages: List[str],
    llm_call: Callable[[List[Dict]], Awaitable[Any]],
    act_prompt: str = DEFAULT_AGENT_ACT_PROMPT,
    max_turns: int = 10
) -> Dict[str, Any]:
    # ...
```
*   **`stages: List[str]`**: 新增参数，一个字符串列表，定义了本次调用的所有认知阶段，例如 `["InnerState", "Actions", "Perform"]`。
*   **`max_turns: int`**: 新增参数，用于控制循环的最大次数，保证安全。

### 3.2 提示词策略 (`act_prompt`)

我们将采用您设计的 `-> STAGE_BEGIN: StageName` 格式作为阶段标记。新的 `DEFAULT_AGENT_ACT_PROMPT` 将会这样引导 LLM：

**新版提示词草案**:

> “你的决策过程必须遵循一个由“阶段标记”驱动的线性流程。阶段标记的格式为 `-> STAGE_BEGIN: StageName`。
>
> 本次任务的阶段顺序是: [这里会动态插入 `stages` 列表，例如：`InnerState`, `Actions`, `Perform`]。
>
> 你的回应必须从第一个阶段 `-> STAGE_BEGIN: InnerState` 开始。在每个阶段标记下，完成该阶段的任务。你可以自行决定何时从一个阶段切换到下一个阶段。你的整个回应应该是一个包含这些标记的、连贯的文本块。
>
> - 在 `Actions` 阶段，你可以调用工具。
> - 如果一个阶段没有内容，请直接省略该阶段的标记。”

### 3.3 核心解析逻辑 (Core Parsing Logic)

`loop` 函数在收到 LLM 的 `response_message` 后，其内部的解析器将按以下通用逻辑工作：

1.  **分割**: 使用正则表达式 `-> STAGE_BEGIN: (\w+)` 来分割 `response_message.content` 文本，得到每个阶段的名称和对应的文本块。
2.  **处理**: 遍历解析出的各个阶段。
3.  **格式化输出**: 对每个阶段的内容进行分析：
    *   获取与本次 LLM 回复关联的 `tool_calls` 列表。
    *   如果当前处理的阶段是 `Actions` 阶段，并且 `tool_calls` 列表不为空，则将该阶段的文本内容和 `tool_calls` 合并成一个交错列表。
    *   如果其他阶段（或 `Actions` 阶段没有工具调用），其内容就是纯文本，则直接作为字符串处理。

### 3.4 最终输出结构 (Final Output Structure)

返回的字典中，`phases` 字段将动态地反映本次调用所使用的阶段，其内部的格式也是动态的。

**新的返回结构示例**:

```python
{
    "status": "success",
    "phases": {
        "InnerState": "用户想知道市场的状态。我需要先获取最新的市场数据，然后再进行分析。",
        "Actions": [
            {
                "type": "tool_call",
                "tool_name": "get_market_data",
                "arguments": {},
                "result": {"price": 100, "volatility": 0.8}
            }
        ],
        "Perform": "根据最新的数据，当前市场价格为100，波动性较高。"
    },
    "full_history": [ ... ]
}
```
*   在这个例子中，`InnerState` 和 `Perform` 阶段因为只包含纯文本，所以它们的值是 `str`。
*   `Actions` 阶段因为包含了工具调用，所以它的值是一个 `list`。

## 4. 调用示例 (Example Usage)

这个设计使得该函数非常灵活，调用者可以即时定义所需的认知流程：

```python
# 定义一个更复杂的认知流程
my_stages = ["Self_Reflection", "Hypothesis", "Action_Plan", "Execution", "Conclusion"]

result = await execute_tool_call_loop(
    instruction="分析最近的销售数据并提出改进建议。",
    stages=my_stages,
    # ... 其他参数
)
```

---

这份设计文档采纳了您的全部新想法，形成了一个高度灵活、健壮且逻辑清晰的 Agent 核心循环方案。请您审阅。