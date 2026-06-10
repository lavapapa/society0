# 设计文档：核心架构关键问题问答 (Q&A)

**版本**: 1.0
**状态**: 已解决

本文档旨在逐一回应在架构设计评审中提出的10个关键问题，并阐述我们最终达成共识的解决方案。

---

### **问题1：当前代码结构的影响范围**

*   **子问题**: 是否应废除 `WorldState`，让 `Environment` 包含 `agents` 和 `globals`？`ExecutionContext` 如何调整？
*   **最终决策**: **不，我们不废除 `WorldState`，而是将其更名为 `World`，并提升其职责。**
*   **解决方案**: 
    1.  `World` 类将作为与 `Schedule` 和 `Persistence` 同级的核心组件，是所有**状态**的唯一真理之源。它内部持有 `environment`, `agents`, `globals` 的真实数据。
    2.  `Environment` 的职责回归纯粹，只管理与环境自身相关的状态。
    3.  因此，`ExecutionContext` **无需做大量调整**，它将继续持有 `context.world`，作为访问所有世界状态的统一、稳定的入口。

---

### **问题2 & 10：`StateProxy` 的具体实现机制与 API 设计**

*   **子问题**: 如何处理嵌套修改、区分不同状态、适配现有访问模式，以及提供怎样的 API？
*   **最终决策**: 我们将**不依赖第三方库，而是自定义一个轻量级的 `DictProxy` 类**，并让 `Agent` 和 `Environment` 的 `state` 等属性本身就成为代理，而非传递临时代理对象。
*   **解决方案**:
    1.  **实现方式**: `Agent` 和 `Environment` 基类将使用 Python 的 **`@property` 装饰器**。当外部代码访问 `agent.state` 时，`@property` 的 getter 方法会动态地创建一个指向 `world.agents[agent_id].state` 真实数据的 `DictProxy` 实例并返回。
    2.  **代理机制**: `DictProxy` 将重写 `__setitem__`, `__getitem__`, `append` 等所有修改和访问方法。当一个修改操作（如 `agent.state['inventory']['food'] += 5`）发生时，代理对象会**立即**将变更应用到 `World` 中的真实数据上，同时捕获完整的修改路径、操作和值，并连同当前的 `ContextStack` 一起，生成一个 `Event` 暂存到当前 `Node` 的事务中。
    3.  **API 设计**: 对开发者而言，API 就是**原生的 Python 字典操作**，无需学习新语法。所有复杂性都被 `DictProxy` 的内部实现所封装。
    4.  **详细设计**: 关于 `DictProxy` 的完整设计，将独立撰写于 `docs/dict_proxy_design.md` 文档中。

---

### **问题3 & 4：上下文栈与事务边界**

*   **子问题**: `ContextStack` 存放在哪？如何传递？如何处理 `Node` 失败与回滚？
*   **最终决策**: `ContextStack` 的根在 `Schedule`，`Node` 级事务失败时**不回滚内存状态，但提交包含失败信息的事件日志**。
*   **解决方案**:
    1.  **存储与传递**: `Schedule` 对象持有“根”上下文栈。在调用链的每一层（`Schedule` -> `StepFlow` -> `Node` -> `Operator` -> `Agent.instruct` -> `Action`/`Behavior`），当前的 `ContextStack` 实例都作为参数**显式传递**下去。
    2.  **并发安全**: `ContextStack` 将被设计为**不可变**的，其 `push` 方法返回一个新的实例，从而天然地支持并发安全。
    3.  **失败处理**: 当一个 `Node` 执行中途抛出异常，框架会：
        a. 捕获异常。
        b. **提交事务**: 将异常发生**之前**所有已暂存的 `Event` 写入日志。
        c. **记录失败**: 生成并写入一个特殊的 `NodeExecutionFailedEvent`，其中包含完整的错误信息。
        d. **不回滚内存**: 已经“立即生效”的内存状态修改将被保留。这符合“失败也是仿真历史一部分”的哲学。

---

### **问题5 & 8：与现有组件的集成**

*   **子问题**: `FunctionRegistry` 中的函数如何获得代理？函数签名如何改变？
*   **最终决策**: 顶层设计保持不变，通过在调用时传递代理对象，实现无缝集成。
*   **解决方案**:
    1.  **代理的传递**: `World` 对象将成为代理的“工厂”。当 `Schedule` 准备调用一个 `Action` 或 `Behavior` 时，它会向 `World` 请求与该 `Action` 相关的 `Agent` 和 `Environment` 的代理版本。
    2.  **函数签名**: `Action`/`Behavior` 的函数签名将变为 `def my_action(agent_proxy, env_proxy, ...)`。开发者在函数内部操作这些代理对象，就如同操作真实对象一样。
    3.  **对开发者的影响**: 表面上，函数签名只是从 `agent` 变成了 `agent_proxy`，但其内部的状态修改行为已经通过代理机制被完全“增强”了。

---

### **问题6 & 11：事件结构的完整定义**

*   **子问题**: 是否需要多种 `Event` 类型？`StateChangeEvent` 是否是最小原子？
*   **最终决策**: 我们不需要预定义大量具体的事件类型。`Event` 的语义应由其上下文提供。我们定义两种基础原子事件。
*   **解决方案**:
    1.  **`StateChangeEvent`**: 记录对 `World` 中任何可序列化状态（`state`, `properties` 等）的一次原子修改。
    2.  **`MemoryChangeEvent`**: 记录对 `Memory` 系统（Milvus）的一次原子修改（增/删/改一个记忆片段）。
    3.  **语义来源**: 一个高层动作（如 `publish_post`）可能会产生多个这样的原子事件。它们的 `context_stack` 将完全相同，通过分析这个共享的上下文栈，我们就能完整地理解这些底层变更背后的高层语义。

---

### **问题7, 9, 14：复杂对象持久化与性能**

*   **子问题**: `Milvus`、`NetworkX` 如何持久化？代理的性能开销？
*   **最终决策**: 采用“快照 + 事件日志”的混合模式，并为复杂对象提供自定义快照接口。性能问题暂时不作为首要优化目标。
*   **解决方案**:
    1.  **`Milvus`**: Milvus Lite 的物理数据库文件将被视为**快照的一部分**。创建快照时，对其进行物理备份；恢复时，进行物理覆盖。
    2.  **`NetworkX`**: `Environment` 的子类需要实现 `snapshot() -> dict` 和 `load_from_snapshot(data: dict)` 两个方法。`PersistenceManager` 在处理 `Environment` 时会自动调用它们。
    3.  **性能**: `StateProxy` 和 `ContextStack` 的开销在现代 Python 中是可接受的，我们优先保证架构的正确性和可追溯性，待未来成为瓶颈时再进行针对性优化。

---

### **问题15：实现策略**

*   **子问题**: 渐进式实现还是一次性重构？
*   **最终决策**: **进行一次完整的、按模块规划的重构，不考虑向后兼容。**
*   **解决方案**: 我们将按照一个清晰的、自下而上的模块顺序进行重构（例如，先 `core_data`，再 `agent` 模块，再 `sim_engine`...），并为新架构编写全新的测试文件。

---

*此文档总结了我们已达成的共识，为后续的详细设计和开发工作提供了清晰的指导。*