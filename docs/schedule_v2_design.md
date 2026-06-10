# 设计文档：Schedule V2 - 支持并行流水线与微型DAG的调度引擎

**版本**: 2.0
**状态**: 最终方案

## 1. 动机与背景 (Motivation & Context)

### 1.1 遇到的问题：僵化的数据流

在对 `Schedule` 模块进行压力测试和复杂场景预演后，我们发现其 V1 版本的数据流模型存在两个根本性的设计缺陷，严重制约了其表达能力和通用性：

1.  **`Operator` 间的“信息孤岛”**: 在一个 `Node` 的 `operators` 列表中，所有 `Operator` 都是并行执行的，它们之间无法进行数据交换。这使得我们无法实现“第一个算子进行测量，第二个算子根据测量结果进行更新”这样的串行依赖逻辑。

2.  **“群体处理”的局限性**: `Operator` 总是对 `Selector` 返回的整个 Agent 列表进行批量处理。这使得我们难以实现针对**每一个** Agent 进行独立、个性化的多步处理流水线，也难以在 `Operator` 之间传递**只属于某个特定 Agent**的上下文信息。

### 1.2 设计目标

本次 V2 架构升级的核心目标，是打破上述限制，将 `Schedule` 引擎从一个简单的“节点执行器”，升级为一个真正强大、灵活的“**通用数据流编排引擎**”。具体目标如下：

*   **节点内数据流**: 支持在一个 `Node` 的 `operators` 列表内部，实现复杂的、非线性的数据依赖和传递。
*   **个体化处理**: 支持对 `Selector` 选中的每一个 Agent，都独立地执行一套完整的 `Operator` 流水线。
*   **统一的查询语言**: 全面采用 `JMESPath` 作为“节点间”和“节点内”数据流的统一查询与转换语言。

## 2. 方案的取舍与演进

在达成最终方案前，我们探讨并否定了几个中间方案：

*   **“管道式 Operator”方案**: 提议让后一个 `Operator` 直接接收前一个的输出。**（否定）** 此方案虽然能解决简单的串行问题，但它破坏了 `Operator` 的输入契约（有些接收 `List[Agent]`，有些接收 `BaseOperatorResult`），且无法处理更复杂的非线性依赖。

*   **“简单的微型 DAG”方案**: 提议让 `Operator` 之间可以通过 `input_mapping` 引用彼此的输出。**（不完备）** 此方案虽然解决了 `Operator` 间的数据流问题，但它仍然是基于“群体处理”的，未能解决“个体化处理流水线”的需求。

最终，在您的关键洞察下，我们将两者结合，形成了最终的“并行 Agent 流水线”方案。

## 3. 最终方案：并行 Agent 流水线 (Parallel Agent Pipelines)

### 3.1 核心思路

我们将 `Node` 的执行逻辑明确地划分为两种模式：

1.  **全局模式 (Global Mode)**: 当 `Selector` 选中 `Environment` 或返回非 Agent 目标时，所有 `Operator` 只执行一次，数据流在 `Node` 级别传递。
2.  **并行 Agent 模式 (Agent Parallel Mode)**: 当 `Selector` 选中一个 Agent 列表时，框架会为**每一个 Agent**，独立地、完整地执行一遍 `operators` 列表，形成多条并行的“个体处理流水线”。

### 3.2 `_execute_node` 的全新执行逻辑

`schedule.py` 中的 `_execute_node` 方法将被彻底重构，其新工作流如下：

1.  **`Selector` 执行**: `selected_agents = await node.selector_func(...)`。
2.  **模式判断**: 根据 `selected_agents` 的内容，决定进入“全局模式”或“并行 Agent 模式”。
3.  **并行 Agent 模式执行**: 
    a. 启动一个外层循环，遍历 `selected_agents` 列表中的每一个 `agent`。
    b. 在循环开始时，为当前 `agent` 创建一个空的、局部的“**个体上下文 (`agent_local_context`)**”字典。
    c. 启动一个内层循环，顺序执行 `Node` 的 `operators` 列表。对于每一个 `operator`：
        i.  **输入映射**: 检查其 `input_mapping` 配置，并使用 JMESPath 从**`agent_local_context`** 中查询数据，注入到该 `operator` 的 `params` 中。
        ii. **执行**: 调用 `operator_func`，并传入**只包含当前 `agent` 的列表 `[agent]`**。
        iii. **存储个体结果**: 将 `operator` 返回的 `BaseOperatorResult`，以其 `id` 为键，存入 `agent_local_context`。
    d. 内层循环结束后，将包含了该 `agent` 所有 `Operator` 执行结果的 `agent_local_context`，追加到一个总的 `node_results` 列表中。
4.  **`Converter` 执行**: 外层循环结束后，将包含所有 Agent 执行结果的 `node_results` 列表，完整地传递给 `Converter` 进行最终的聚合与转换。

### 3.3 方案优势

*   **两全其美**: 完美地结合了“批量选择”的高效率和“个体化处理”的灵活性。
*   **强大的数据流**: 同时支持“节点内个体上下文”和“节点间群体上下文”的传递，能够应对极其复杂的仿真逻辑。
*   **职责清晰**: `Operator` 的实现者只需关注处理单个 Agent 的逻辑，框架在后台自动处理了所有并行的复杂性。

## 4. 关键细节与实现思路

### a. `schedule.yaml` 的新配置

`operators` 列表中的每个条目，现在是一个拥有 `id` 和可选 `input_mapping` 的对象。

**用例**: 实现您设想的“测量-更新”闭环。
```yaml
nodes:
  - id: measure_and_update_trust
    selector: { type: all_agents }
    operators:
      - id: survey_op # 为 operator 增加 ID
        type: instruct
        instruction: "请完成以下信任问卷..."
        output_schema: { ... } # 定义结构化输出

      - id: update_op
        type: behavior
        name: "update_digital_trust"
        # 关键：使用 JMESPath 从上一个 operator 的结果中精确提取数据
        input_mapping:
          # 将 survey_op 的结构化输出，映射为 update_op 的 trust_data 参数
          trust_data: "survey_op.structured_output"

    converter:
      type: jmespath
      # 对所有 agent 的 update_op 结果进行最终聚合
      expression: "{avg_trust: avg([*].context.update_op.value.new_trust_score)}"
```

### b. `BaseOperatorResult` 的重要性

我们之前设计的 `BaseOperatorResult` 在此方案中至关重要。`Converter` 在对最终的 `node_results` 列表进行 JMESPath 查询时，它查询的就是这些结构化对象的字段（如 `status`, `value`, `error_message`），这保证了数据查询的稳定可靠。

### c. 错误处理

在“并行 Agent 模式”下，如果针对某个 Agent 的 `Operator` 链条执行失败，框架会将该 Agent 的执行结果标记为 `status: "FAILURE"`，但**不会**中断其他 Agent 的执行。这保证了系统的韧性。

## 5. 总结

通过引入“并行 Agent 流水线”和“节点内微型 DAG”的设计，`Schedule V2` 架构将具备前所未有的灵活性和表达能力。它将数据流的控制权以一种声明式、高度一致（统一使用 JMESPath）的方式完全交给了用户，同时通过 `BaseOperatorResult` 等契约保证了系统的健壮性。这是一个足以支撑未来各种复杂仿真需求的、坚实可靠的调度引擎架构。