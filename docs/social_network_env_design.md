# 设计文档：`SocialNetworkEnv` 增强方案

**版本**: 1.0
**状态**: 最终方案

## 1. 背景与设计目标

### 1.1 背景

在完成了对 `SimEngine` 核心架构（`World`, `StateProxy`, `Schedule` 等）的重构后，我们需要一个功能完备、设计优雅的 `Environment` 实现，来作为新架构的“黄金路径”验证案例，并为未来的仿真研究提供一个坚实的基础。`SocialNetworkEnv` 正是为此而生。

### 1.2 设计目标

1.  **通用性**: `SocialNetworkEnv` 应提供一个社交网络环境所必须的、通用的基础设施和原子能力，而非针对某个特定研究的实现。
2.  **可配置性**: 环境的拓扑结构、功能开关等应完全可配置，并能从外部配置文件中读取。
3.  **职责分离**: 严格区分“通用能力”（由 `Env` 自身提供）和“特定法则”（由外部研究脚本定义），`Env` 不应包含任何硬编码的研究逻辑。
4.  **框架兼容**: 完美适配我们新设计的“统一状态架构”，包括 `StateProxy` 的使用、`snapshot` 持久化接口的实现，以及 `Action` 的装配机制。

---

## 2. 核心设计与实现思路

### 2.1 初始化与配置 (`__init__` & `initialize`)

**原则**: 配置的接收与状态的“创世”分离。

*   **`__init__(self, world: World)`**:
    *   **职责**: 只负责从 `world.environment_data` 中接收和验证配置。
    *   **实现**: 
        1.  它会从 `world.environment_data['state']` 中获取原始的 `config` 字典。
        2.  使用 `pydantic` 的 `SocialNetworkConfig.model_validate(config)` 对配置进行严格的解析和验证。
        3.  将验证通过的、类型安全的 `config` 对象，存储在一个私有实例属性 **`self._config`** 中。**我们明确，配置信息不应污染 `state` 属性。**
        4.  初始化一个实例属性 **`self.graph: nx.DiGraph = None`**，用于存放网络图。**我们明确，`nx.DiGraph` 对象本身不存放在 `state` 中**，因为它不是可直接序列化的。

*   **`initialize(self, agents: List[Agent])`**:
    *   **职责**: 这是由 `SimEngine` 调用的“创世”方法，负责根据 `self._config`，真正地构建世界的初始状态。
    *   **实现**: 
        1.  调用一个私有的 `_generate_topology(agents)` 方法，该方法将是拓扑生成的核心。
        2.  `_generate_topology` 会读取 `self._config.distribution`，并**实现您论文中基于 `CV` 值的强/弱/无连接网络生成逻辑**。它会先生成一个基础图，然后通过迭代算法调整边的连接，直到整个网络的拓扑结构符合预设的 `CV` 值分布目标。
        3.  将最终生成的 `nx.DiGraph` 对象赋值给 `self.graph`。
        4.  如果 `self._config.social_media.enabled` 为 `True`，则通过 `StateProxy` 初始化环境状态：`self.state['posts'] = {}`，`self.state['reports'] = []` 等。

### 2.2 `Action` (技能) 的完整实现

`SocialNetworkEnv` 将通过 `get_actions()` 方法，向 `World` 的装配系统提供一套完整的、原子化的基础社交 `Action`。所有 `Action` 的实现都将通过 `StateProxy` 来触发事件和修改状态。

*   **`publish_post(self, agent, content, tags, reply_to=None)`**:
    *   **（新设计）** 增加一个可选的 `reply_to: Optional[str] = None` 参数，用于实现“转发”或“评论”的功能。如果提供了 `reply_to`，则生成的 `Post` 对象会记录其父帖子的 `id`。
    *   通过 `env_proxy.state['posts'][new_post.id] = new_post.model_dump()` 来添加帖子。

*   **`like_post(self, agent, post_id)`**:
    *   通过 `env_proxy.state['posts'][post_id]['likes'].append(agent.id)` 来添加点赞。
    *   ⚠️ `posts` 始终保持为 `{post_id: {...}}` 的字典结构，所有 Logic 代码必须复用上述 Action，而不是将 `posts` 视为列表或重新赋值。

*   **`follow(self, agent, target_agent_id)`**:
    *   **关键实现**: 这个 `Action` 的实现将**不会**直接操作 `self.graph` 对象。相反，它会通过 `StateProxy` 触发一个特殊的、可被 `PersistenceManager` 和 `Event` 系统理解的操作。
    *   **示例**: `env_proxy.state['graph'].add_edge(agent.id, target_agent_id)`。`DictProxy` 在拦截到对 `graph` 属性的访问时，会返回一个特殊的 `GraphProxy`，其 `add_edge` 方法会生成一个专门的 `GraphChangeEvent`。

*   **`report_post(self, agent, post_id, reason)`**:
    *   创建一个“举报”记录字典。
    *   通过 `env_proxy.state['reports'].append(report_data)` 将其添加到举报列表中。

### 2.3 `FoV` (视野) 的实现

`SocialNetworkEnv` 将提供 Agent 感知世界所需的所有视野函数。这些函数都是只读的，它们直接访问 `self.graph` 和 `self.state` 来获取数据。

*   `get_recommended_feed(self, agent, env)`: 实现完整的个性化推荐算法。
*   `get_trending_posts(self, env)`: 实现热榜算法。
*   `get_post_details(self, post_id, env)`: 查看单个帖子详情。

### 2.4 与框架的集成接口

*   **`get_actions(self) -> Dict`**: 
    *   此方法将返回一个字典，其中包含了 `publish_post`, `like_post` 等所有 `Action` 的完整定义，包括它们的实现函数 (`self.publish_post`) 和符合 OpenAI 规范的参数 `schema`。

*   **`snapshot(self) -> Dict`**: 
    *   **职责**: 将所有**非 `state`** 的、需要持久化的复杂对象，转换为可序列化的字典。
    *   **实现**: `return {"graph": nx.to_dict_of_lists(self.graph)}`。

*   **`restore_from_snapshot(self, data: Dict)`**: 
    *   **职责**: 从 `snapshot` 数据中重建复杂对象。
    *   **实现**: `self.graph = nx.from_dict_of_lists(data.get("graph", {}))`。

## 3. 总结

这份增强方案将 `SocialNetworkEnv` 打造成了一个功能强大、职责清晰、且完全融入我们新框架的“通用社交网络基础设施”。它为上层的“研究特定”逻辑（如 `Behavior` 和 `Schedule` 配置）提供了一个稳定可靠的运行平台。
