# 设计文档：模块化记忆系统架构

**版本**: 3.0
**状态**: 最终方案

## 1. 核心设计原则

经过多轮讨论，我们确定了记忆系统的核心设计原则，旨在实现高度模块化、可扩展和职责分离。

1.  **记忆即服务 (Memory as a Service)**: 记忆系统是一个完全独立的模块。它封装了所有关于记忆的存储（Saving）、召回（Retrieval）和遗忘（Forgetting）的复杂逻辑，并对外提供简洁的接口。它不依赖，也不知道任何上层业务逻辑（如 `ToolCallLoop`）。

2.  **Agent 即装配者 (Agent as Assembler)**: `LLMAgent` 是顶层的协调者和“装配者”。它负责协调其拥有的各个模块，其工作流是：先调用**记忆系统**获取上下文，然后将上下文与任务指令**组装**起来，最后再将信息传递给**推理引擎**（`ToolCallLoop`）。

3.  **数据驱动交互 (Data-Driven Interaction)**: 模块之间通过标准化的数据结构（如记忆片段、工具调用结果）进行通信，而非紧耦合的函数调用链。

## 2. 架构设计

### 2.1 存储后端 (Storage Backend)

*   **技术选型**: 采用 **Milvus Lite** 作为本地向量数据库。它功能强大且易于集成。
*   **数据隔离策略**: 采纳**“一个 Agent 一个 Collection”**的方案。每个 Agent 的所有记忆将存储在以其 `agent_id` 命名的专属 Milvus Collection 中。这提供了最佳的查询性能和数据隔离。

### 2.2 记忆片段 (Memory Entry) 数据结构

存储在 Milvus 中的每个记忆片段都是一个标准化的文档，其结构如下：

```json
{
    "id": "mem_uuid_123",
    "type": "episodic" | "semantic",
    "content": "对此记忆的文本描述。",
    "embedding": [0.1, 0.2, ...],
    "timestamp": 10, // 记忆创建时的时间步 (step)
    "base_importance": 0.8, // 由LLM或启发式规则给出的基础重要性评分
    "metadata": {
        "branch_id": "main", // 用于支持未来的“平行时空”能力
        "source_node_id": "bob_posts_something" // 可选，记录该记忆产生的 schedule 节点
    }
}
```
*   **注意**: `agent_id` 不再需要，因为它由 Collection 的名称隐含。
*   `timestamp` 即 `step`，是实现遗忘和新近度加权的关键。

## 3. 核心机制

### 3.1 写入机制 (Saving)

记忆的写入由 `LLMAgent` 在其工作流中主动调用 `Memory` 模块的接口来完成。

*   **情景记忆 (Episodic)**: 在 `ToolCallLoop` 执行完毕后，`LLMAgent` 会将本次任务的总结（例如，基于 `phases` 结果生成）作为情景记忆，调用 `memory.add_episodic_memory()` 进行存储。
*   **语义记忆 (Semantic)**: 当 `LLMAgent` 执行“反思”工作流时，它会将最终从 LLM 得到的“洞见”或“知识点”，调用 `memory.add_semantic_memory()` 进行存储。

### 3.2 遗忘机制 (Forgetting)

采用基于“遗忘曲线”的动态评分机制，而非物理删除。

*   **当前相关性 (Current Relevance)**: 在召回时，每个记忆的“当前相关性”分数会被动态计算：
    `CurrentRelevance = base_importance * exp(-decay_rate * (current_step - timestamp))`
*   `decay_rate` 是一个可配置的超参数，用于控制遗忘速度。
*   这个 `CurrentRelevance` 分数将作为召回排序的关键因子之一。

### 3.3 召回机制 (Retrieval)

召回是记忆系统最核心的“读取”功能，由 `LLMAgent` 在调用推理引擎**之前**执行。

*   **接口**: `Memory` 类提供一个核心方法 `retrieve(query: str, top_k: int) -> List[str]`。
    *   注意：`branch_id` 和 `agent_id` (collection_name) 应由 `Memory` 实例在初始化时持有，无需在每次 `retrieve` 时传入。
*   **内部逻辑**: `retrieve` 方法会执行一个混合搜索：
    1.  将 `query` 文本转换为查询向量。
    2.  在对应的 Collection 中，根据“向量相似度”和动态计算的“当前相关性”进行加权排序。
    3.  返回 Top-K 个最相关记忆的 `content` 文本列表。

## 4. Agent 记忆工具 (Agent Tools)

为让 Agent 能主动与记忆交互，`Memory` 类将实现并提供以下工具。`LLMAgent` 在初始化时会从 `Memory` 实例中获取这些工具，并加入到自己的 `ToolSet` 中。

*   `@agent_tool def search_memory(query: str) -> List[str]`: **搜索记忆**。允许 Agent 根据关键词，精确搜索自己的记忆库。
*   `@agent_tool def reflect_on_topic(topic: str) -> str`: **针对主题反思**。主动触发一次“反思”流程，生成关于特定主题的语义记忆。
*   `@agent_tool def memorize_this(fact: str, importance: float)`: **强制记忆**。允许 Agent 将一条信息作为高重要度的语义记忆直接存入。

## 5. 最终工作流 (Final Workflow)

`LLMAgent` 的单步决策完整工作流如下：

1.  **接收任务**: 从 `Schedule` 获得原始 `instruction`。
2.  **调用记忆**: `relevant_memories = memory.retrieve(query=instruction)`。
3.  **组装提示**: 将 `relevant_memories` 和 `instruction` 组装成一个丰富的“用户提示”。
4.  **调用推理**: `loop_result = execute_tool_call_loop(instruction=..., tool_set=..., ...)`。
5.  **解析结果**: 从 `loop_result` 中提取出 `performative_output` 和 `actions_taken`。
6.  **执行表现**: 将 `performative_output` 作为 Agent 在世界中的最终表现。
7.  **写入记忆**: 根据 `loop_result` 生成总结，调用 `memory.add_episodic_memory()`。
