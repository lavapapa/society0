# 设计文档：专用的 `interview` 访谈操作符

**作者:** Gemini

**日期:** 2025年9月20日

**状态:** 提案

## 1. 摘要

当前，在仿真中对智能体进行“测量”或“访谈”是通过通用的 `instruct` 操作符完成的。然而，这种方法存在两个潜在的设计缺陷，可能污染实验数据：

1.  **动作污染:** 在访谈期间，智能体理论上仍有能力执行其行动集（ActionSet）中的所有动作（例如，社交动作），这并非访谈的意图。
2.  **记忆污染:** 访谈行为本身会被智能体作为一段新的经历记录到其记忆系统中，这可能会影响它在后续步骤中的行为和回答。

本文档提出了一个设计方案，通过创建一个新的、专用的 `interview` 操作符来解决这些问题。该方案旨在以最小的架构变动和最大化的代码复用，实现一个功能清晰、行为安全的智能体访谈机制。

## 2. 核心目标

1.  **功能分离:** 将“命令智能体行动” (`instruct`) 和“访谈智能体以获取信息” (`interview`) 的意图在架构层面明确分开。
2.  **确保测量纯净性:** 在执行 `interview` 操作时，必须保证：
    *   智能体**可以访问和回顾**其现有记忆，以便真实地回答问题。
    *   智能体**不能**在访谈期间执行任何可能改变世界状态的动作（例如社交、交易等）。
    *   访谈这个事件本身**不能**被智能体作为新的记忆存储下来。
3.  **最大化代码复用:** 新功能的实现应尽可能重用现有的认知循环、提示词构建和动作执行逻辑。

## 3. 详细设计方案

我们将通过引入一个新的操作符类型，并对现有的认知流程参数进行微调来实现目标。

### 3.1. 步骤一：增强认知方法的参数

为了实现对记忆读写的精细化控制，我们将对 `LLMAgent` 的核心认知方法进行以下修改：

-   **文件:** `src/simengine/agent/core.py`
-   **类:** `LLMAgent`
-   **方法:** `async def instruct(...)`

**修改内容:**
1.  将现有的 `is_memory: bool = True` 参数重命名为 `retrieve_memory: bool = True`，使其含义更精确，专注于控制记忆的**读取/检索**。
2.  新增一个 `save_memory: bool = True` 参数，专门用于控制记忆的**写入/存储**。

**修改后签名:**
```python
# from
async def instruct(self, ..., is_memory: bool = True, ...):
# to
async def instruct(self, ..., retrieve_memory: bool = True, save_memory: bool = True, ...):
```

**逻辑调整:**
-   方法内原先由 `is_memory` 控制的**记忆检索**逻辑，现在由 `retrieve_memory` 控制。
-   方法内原先由 `is_memory` 控制的**记忆存储**逻辑，现在由 `save_memory` 控制。

### 3.2. 步骤二：创建新的 `interview` 接口

我们将沿着 `instruct` 的调用链，创建一条并行的 `interview` 调用链。

#### 3.2.1. 在 `LLMAgent` 中创建 `interview` 方法

-   **文件:** `src/simengine/agent/core.py`
-   **类:** `LLMAgent`

**实现:**
-   创建一个新的 `async def interview(...)` 方法。
-   该方法将是 `instruct` 方法的一个近乎完整的副本，以重用其复杂的提示词构建和调用 `execute_action_loop` 的逻辑。
-   它将接受与 `instruct` 相同的参数。

#### 3.2.2. 在 `World` 中创建 `interview_agent` 方法

-   **文件:** `src/simengine/core_data.py`
-   **类:** `World`

**实现:**
-   创建一个新的 `async def interview_agent(...)` 方法，作为 `interview` 操作的统一入口。
-   该方法将调用 `agent.interview(...)`，并**硬编码**以下关键参数，以从架构层面保证访谈的纯净性：
    -   `retrieve_memory=True`  **(允许)**
    -   `save_memory=False`     **(禁止)**
    -   `action_tags=[]`        **(禁止)**
-   这将确保任何通过 `interview_agent` 发起的调用都无法写入记忆或执行除 `finish_instruction` 之外的任何动作。

### 3.3. 步骤三：注册新的 `interview` 操作符

-   **文件:** `src/simengine/schedule.py`
-   **类:** `StepFlow`

**实现:**
-   在 `_compile_operator` 方法中，增加对 `type: interview` 的支持。
-   当检测到 `interview` 类型时，它将被编译成一个调用 `world.interview_agent(...)` 的操作符函数。

### 3.4. 步骤四：在仿真研究中应用

-   **文件:** `studies/misinformation_study/schedule.yaml`

**修改:**
-   将 `measurement_phase` 节点中的 `survey_op` 操作符类型从 `instruct` 改为 `interview`。
-   为了语义清晰，将 `instruction` 字段重命名为 `question`。

**示例:**
```yaml
# ...
- id: measurement_phase
  dependencies: [agent_interaction_phase]
  selector:
    type: all_agents
  operators:
    - id: survey_op
      type: interview           # <-- 使用新的操作符
      question: "请根据你最近的经历..." # <-- 语义更清晰的字段
      output_schema: { ... }
    # ...
```

## 4. 方案优势

1.  **意图明确:** 在 Schedule 中使用 `type: interview` 能清晰地表达开发者的意图，增强了配置的可读性和可维护性。
2.  **设计稳健:** 通过在 `World` 层硬编码核心安全参数 (`save_memory`, `action_tags`)，从根本上杜绝了因配置失误导致数据污染的可能性。
3.  **高度复用:** 该方案几乎完全重用了现有的、经过测试的认知循环 (`execute_action_loop`) 和提示词工程，改动成本低，风险小。
4.  **易于扩展:** 建立了一个清晰的设计模式，未来可以轻松地仿照此模式添加更多专用的、安全的智能体交互类型（如 `trade`, `negotiate` 等）。

## 5. 结论

该方案通过引入一个专用的 `interview` 操作符，并对记忆控制参数进行微调，以一种优雅且安全的方式解决了测量污染问题。它不仅修复了当前的设计缺陷，还提升了仿真引擎的长期可扩展性和设计的清晰度。
