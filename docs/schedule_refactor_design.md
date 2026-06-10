# 设计文档：Schedule 模块架构升级

**版本**: 1.0
**状态**: 最终提案

## 1. 背景与问题 (Background & Problem Statement)

### 1.1 上下文

`Schedule` 模块是 `SimEngine` 的核心编排引擎。它负责将用户在 `schedule.yaml` 中定义的、由节点（Node）组成的有向无环图（DAG），转化为可执行的、分步骤的仿真流程。其核心职责是管理节点间的数据流，确保数据能够从上游节点的输出，正确地传递和转换为下游节点的输入。

### 1.2 遇到的问题

在对当前实现进行深入分析后，我们发现两个关键问题，限制了框架的灵活性、健壮性和扩展性：

1.  **数据转换能力孱弱且僵化**: 当前的数据转换依赖于两套独立的、功能有限的机制：
    *   **参数模板**: 简单的 `{node.output}` 字符串替换，无法处理嵌套数据、列表筛选或结构转换等复杂场景。
    *   **`Converter` 类型**: 硬编码的 `aggregate`, `filter` 等 Python 函数，每当用户有新的转换需求时，都必须由框架开发者添加新的 `Converter` 类型，用户无法自行扩展。
    *   **违背原则**: 这违背了我们追求“通用”和“灵活”的设计原则。

2.  **数据契约缺失**: 从 `Operator` 到 `Converter` 的数据传递，目前依赖于一个非结构化的 `Dict[str, Any]`。这意味着 `Operator` 可以随心所欲地返回任何形状的数据，而下游的 `Converter` 只能“猜测”它会收到什么。这种“隐式契约”非常脆弱，难以调试，并且在 `Operator` 的实现发生变化时，极易引发连锁性的、难以发现的错误。
    *   **违背原则**: 这违背了系统设计中“接口清晰、契约明确”的健壮性原则。

## 2. 设计目标

本次架构升级旨在达成以下目标：

1.  **引入标准**: 引入一个业界公认的、强大的标准数据查询语言，统一并增强框架的数据处理能力。
2.  **明确契约**: 在框架的核心数据流路径上，建立清晰、强类型的“数据契约”，提升系统的稳定性和可维护性。
3.  **提升灵活性**: 将数据转换的灵活性完全交给用户，使其能够通过配置文件，轻松实现任意复杂的数据处理，而无需修改框架代码。

---

## 3. 方案一：全面采用 JMESPath 统一数据处理

此方案旨在解决“数据转换能力孱弱”的问题。

### a. 技术选型与依赖

*   **我们选择 JMESPath**。它是一个专门为 JSON（以及与之结构相同的 Python Dict）设计的、功能强大的查询和转换语言，已被 AWS 等大型平台广泛采用，并拥有成熟的 Python 实现库 (`jmespath`)。
*   **技术依赖**: 本方案将为框架引入一个新的外部依赖 `jmespath`。

### b. 设计详述

我们将用 JMESPath **完全取代**现有的参数模板和 `Converter` 类型。

1.  **改造 `Input` 与参数模板**: 
    *   在 `schedule.yaml` 中，所有需要引用上游数据的参数值，将不再使用 `{...}` 格式，而是直接使用 JMESPath 表达式字符串。
    *   框架中的 `_render_template` 函数将被重构为一个通用的 JMESPath 求值器。
    *   **示例**: 
        ```yaml
        # 旧方案
        params: 
          some_value: "{node_a.output.results[0].value}" # 无法实现
        # 新方案
        params:
          some_value: "node_a.output.results[0].value" # JMESPath 表达式
        ```

2.  **改造 `Converter`**:
    *   我们将**废弃**所有内置的 `aggregate`, `filter`, `summary` 等 `Converter` 类型。
    *   取而代之的是一个**唯一的、通用的 `jmespath` 转换器**。它只接收一个参数 `expression`。
    *   **示例**:
        ```yaml
        # 旧方案
        converter:
          type: aggregate
          aggregation_type: count
        # 新方案
        converter:
          type: jmespath
          expression: "length(@)" # `@` 代表完整的输入数据
        ```

### c. 破坏性分析

*   这是一个**必要的、重大的破坏性变更**。所有现存的 `schedule.yaml` 配置文件都必须被重写，以遵循新的 JMESPath 语法。但这次一次性的重构，将换来未来无穷的灵活性和能力扩展。

---

## 4. 方案二：使用 `BaseOperatorResult` 规范数据契约

此方案旨在解决“数据契约缺失”的问题。

### a. 设计详述

*   我们将在 `core_data.py` 中定义一个新的 `dataclass`，名为 `BaseOperatorResult`。
*   这个类将作为所有 `Operator` 函数返回值的**强制性基类**，建立一个清晰的“数据契约”。
*   **结构定义**:
    ```python
    @dataclass
    class BaseOperatorResult:
        agent_id: str
        status: str  # 例如: "SUCCESS", "FAILURE"
        error_message: Optional[str] = None
        # 一个灵活的 data 字段，用于存放该 Operator 特有的、非结构化的返回数据
        data: Dict[str, Any] = field(default_factory=dict)
    ```
*   我们之前为 `instruct` 设计的 `InstructOperatorResult`，可以作为这个基类的一个更具体的实现或子类。

### b. 协作与依赖

*   **对 `Operator` 的要求**: 所有 `Operator` 的实现者（包括我们自己和未来的用户），都**必须**确保其函数返回一个 `BaseOperatorResult` 的实例。
*   **对 `Converter` 的影响**: `Converter` 的输入类型签名将从 `List[Dict]` 变为 `List[BaseOperatorResult]`。当 `Converter` 使用 JMESPath 表达式时，它可以精确地查询 `status`, `data` 等字段，例如 `[?status=='SUCCESS'].data`。

### c. 破坏性分析

*   这也是一个**必要的破坏性变更**。它要求我们重构所有现存的 `Operator` 实现，以遵循新的返回类型契约。然而，这种规范化带来的健壮性和可维护性收益，远大于一次性的重构成本。

## 5. 总结

这两个方案相辅相成。方案一（JMESPath）提供了前所未有的**数据处理能力和灵活性**，方案二（`BaseOperatorResult`）则为这条强大的数据流提供了**稳定和可靠的“河道”**。共同实施后，`Schedule` 模块将演变为一个真正现代、强大且优雅的仿真编排引擎。