# 设计文档：资源管理器与依赖注入

**版本**: 1.0
**状态**: 最终方案

## 1. 背景与问题

随着框架日益成熟，我们面临两个关键的工程化挑战，它们限制了系统的可扩展性、并发能力和可测试性：

1.  **硬编码的外部服务调用**: 当前的 `LLMAgent` 和 `Memory` 模块直接依赖于一个写死的、单一的 `llm_call` 或 `embed_call` 函数。这使得我们无法管理多个、具有不同配置和并发限制的外部模型端点。

2.  **全局状态的滥用**: `Memory` 模块通过全局函数 (`get_global_milvus`) 来获取 Milvus 客户端实例。这种全局状态使得并行运行多个独立仿真变得不可能，并且极大地增加了单元测试的难度和耦合度。

本设计旨在通过引入“资源管理器”和“依赖注入”模式，来系统性地解决这些问题。

## 2. 方案一：LLM 与 Embedding 管理器

### a. 设计目标

*   实现对多个 LLM 和 Embedding 端点的统一管理。
*   为每个端点实现独立的并发控制。
*   提供一个统一的、负载均衡的调用接口。
*   将总的 LLM 并发能力反馈给 `Schedule`，以控制仿真并行度。

### b. 解决方案：引入 `LLMManager` 和 `EmbeddingManager`

我们将创建两个新的、独立的管理器类。

#### `LLMManager` 类

*   **职责**: 统一管理所有对大语言模型的 API 调用。
*   **初始化**: `__init__(self, endpoints: List[Dict])`
    *   接收一个端点配置列表。每个配置项都是一个 `openai.AsyncClient` 兼容的参数字典，并额外包含一个 `concurrency` 字段。
        ```json
        {
            "id": "openai_default",
            "api_key": "sk-...",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4-turbo",
            "concurrency": 10 
        }
        ```
    *   在内部，它会为每个端点创建一个 `asyncio.Semaphore(concurrency)` 用于并发控制。
*   **核心接口**: `async request(self, payload: Dict) -> Dict`
    *   **内部逻辑**: 使用轮询等策略选择一个可用端点，通过 `async with endpoint.semaphore:` 获取调用许可，然后使用 `openai.AsyncClient` 发起请求。
*   **辅助接口**: `get_total_concurrency() -> int`
    *   返回所有端点 `concurrency` 的总和。

#### `EmbeddingManager` 类

*   **设计**: 与 `LLMManager` 完全相同，但其 `request` 方法接收待 embedding 的文本列表。

### c. 与框架的集成

1.  **`main.py`**: 在启动脚本中，我们将实例化 `LLMManager` 和 `EmbeddingManager`。
2.  **`SimEngine`**: `SimEngine` 在初始化时，会从 `LLMManager` 获取 `total_concurrency`，并用此值来初始化一个用于控制 `Node` 并行度的 `asyncio.Semaphore`。
3.  **`LLMAgent`**: 在初始化 `LLMAgent` 的认知系统时，我们会将 `llm_manager.request` 和 `embedding_manager.request` 这两个方法，作为 `llm_call` 和 `embed_call` **注入**进去。

---

## 3. 方案二：Milvus 客户端的去全局化

### a. 设计目标

*   彻底移除对全局 Milvus 客户端的依赖。
*   确保每个仿真实验都拥有自己独立的、隔离的向量数据库。
*   提高系统的可测试性。

### b. 解决方案：依赖注入与职责转移

我们将 Milvus 客户端的管理权完全交给 `PersistenceManager`。

1.  **废除全局**: **删除** `agent/memory.py` 中的 `init_global_milvus` 和 `get_global_milvus` 函数及相关的全局变量。

2.  **`PersistenceManager` 的新职责**:
    *   在其 `__init__` 方法中，它将根据传入的 `save_dir`，创建并持有一个**属于本次仿真**的 `MilvusClient` 实例。例如：`self.milvus_client = MilvusClient(uri=f"{self.save_dir}/milvus.db")`。

3.  **`World` 作为桥梁**:
    *   `SimEngine` 在创建 `World` 对象时，会将 `persistence_manager.milvus_client` 注入到 `World` 中。

4.  **`Memory` 接收注入**:
    *   `Memory.__init__` 的签名将被修改，直接接收一个 `milvus_client` 实例作为参数。
    *   `World` 在为 `LLMAgent` 创建 `Memory` 实例时，会将自己持有的 `milvus_client` 实例传递进去。

### c. 优势

*   **完全隔离**: 每个 `SimEngine` 实例都拥有自己独立的持久化路径和向量数据库，可以安全地并行运行多个实验。
*   **职责清晰**: `PersistenceManager` 统一负责所有持久化资源，`Memory` 只关心记忆逻辑。
*   **易于测试**: 在单元测试中，可以轻松地将一个 mock 的 `MilvusClient` 对象注入到 `Memory` 中，无需依赖任何全局状态。

---

*此文档确立了将外部服务和资源进行中心化管理、并通过依赖注入与核心模块解耦的设计原则，是框架走向生产级健壮性的关键一步。*