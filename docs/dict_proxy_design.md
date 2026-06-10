# 设计文档：`DictProxy` - 上下文感知型状态代理

**版本**: 1.0
**状态**: 最终提案

## 1. 背景与设计目标

### 1.1 问题陈述

在我们的“统一状态架构”中，核心需求是：当 `Action` 或 `Behavior` 函数修改状态时，框架必须能够自动、无感知地捕获这次修改的**完整上下文**（调用栈）和**具体内容**（修改路径、操作、值），并将其记录为富语义事件。

直接让开发者手动创建 `StatePatch` 或 `Event` 对象是繁琐且易错的。因此，我们需要一个“代理”机制，让开发者能以操作普通 Python 字典或对象的方式来修改状态，而所有复杂的捕获和记录工作都在后台自动完成。

### 1.2 设计目标

1.  **透明的开发者体验**: 对 `Action`/`Behavior` 的开发者来说，通过代理对象修改状态的语法，应与操作原生 Python 字典和对象几乎完全一致。
2.  **完整的变更捕获**: 代理必须能捕获所有类型的修改操作，包括项目设置 (`d[k]=v`)、属性设置 (`d.k=v`)、列表操作 (`append`, `extend`, `pop`) 以及深层嵌套对象的修改。
3.  **上下文感知**: 代理在捕获变更时，必须能够访问到当前的 `ContextStack`。
4.  **事件生成**: 代理的核心职责是生成一个包含“变更内容”和“上下文”的 `Event`，并将其提交给当前 `Node` 的事务处理器。
5.  **立即生效**: 为了保证后续逻辑的正确性，代理所做的修改必须立即反映到内存中的真实状态对象上。

## 2. 核心设计：`DictProxy` 与 `ListProxy`

我们将不依赖第三方库，而是自定义两个轻量级的代理类来实现此功能。

### 2.1 `DictProxy` 类

这是代理系统的核心。它代理一个字典对象。

**初始化签名**:
`__init__(self, target_dict: dict, event_recorder: Callable, context_provider: Callable, path: Tuple[str, ...])`

*   `target_dict`: 对 `World` 中真实字典的引用。
*   `event_recorder`: 一个回调函数，当变更发生时，代理会调用它来记录事件。它接收一个 `Event` 对象作为参数。
*   `context_provider`: 一个回调函数，调用它能获取到当前的 `ContextStack`。
*   `path`: 一个元组，表示当前代理在整个状态树中的路径（例如 `('agents', 'alice', 'state')`）。

**核心实现原理**:

*   **拦截修改方法**: `DictProxy` 会重写所有会修改字典内容的方法，包括：
    *   `__setitem__(self, key, value)`
    *   `__delitem__(self, key)`
    *   `update(self, other_dict)`
    *   `pop(self, key)`
    *   `clear(self)`
*   **在重写方法中**: 以 `__setitem__` 为例：
    1.  获取当前上下文: `context_stack = self.context_provider()`
    2.  构建 `Event`: 创建一个 `StateChangeEvent`，其中包含：
        *   `change`: `{"path": self.path + (key,), "op": "set", "value": value}`
        *   `context_stack`: `context_stack`
    3.  **记录事件**: 调用 `self.event_recorder(event)`，将事件提交给当前 `Node` 的事务处理器。
    4.  **立即生效**: 在真实的 `target_dict` 上执行操作：`self.target_dict[key] = value`。

*   **处理嵌套（关键）**: `__getitem__` 方法是实现深层代理的关键。
    *   当代码访问 `proxy['inventory']` 时，`__getitem__` 会被调用。
    *   它会检查 `self.target_dict['inventory']` 的值。
    *   如果这个值是另一个字典，`__getitem__` **不会**直接返回这个字典，而是会**创建一个新的 `DictProxy` 实例**，并用更新后的路径 `self.path + ('inventory',)` 来初始化它，然后返回这个新的代理实例。
    *   如果值是一个列表，则同理返回一个 `ListProxy` 实例。
    *   这样就实现了代理的**递归创建**，无论访问多深，得到的永远是代理对象。

### 2.2 `ListProxy` 类

`ListProxy` 专门用于代理列表对象，其原理与 `DictProxy` 完全相同。

*   **初始化签名**: 与 `DictProxy` 类似，但接收一个 `target_list`。
*   **拦截修改方法**: 它会重写 `append`, `extend`, `insert`, `remove`, `pop`, `__setitem__`, `__delitem__` 等所有列表修改方法。
*   **在重写方法中**: 执行与 `DictProxy` 相同的“生成 Event -> 记录 Event -> 立即生效”的流程。
*   **处理嵌套**: 同样通过重写 `__getitem__` 来实现对列表内字典或列表的递归代理。

## 3. 在框架中的应用

### 3.1 `Agent` 与 `Environment` 的改造

我们将使用 Python 的 `@property` 装饰器，以一种极其优雅的方式将代理机制集成进去。

```python
# in World class
class World:
    def __init__(...):
        self.agents_data = { ... } # 存储真实的 agent dict 数据
        self.env_data = { ... }    # 存储真实的 env dict 数据
        self._agents_cache = {}

    def agent(self, agent_id: str) -> Agent:
        # 使用缓存避免重复创建 Agent 实例
        if agent_id not in self._agents_cache:
            self._agents_cache[agent_id] = Agent(agent_id, self)
        return self._agents_cache[agent_id]

# in Agent class
class Agent:
    def __init__(self, agent_id: str, world: World):
        self.id = agent_id
        self._world = world # 持有对真实世界源的引用

    @property
    def state(self) -> DictProxy:
        # 每次访问 .state 属性时，都会动态创建一个代理
        return DictProxy(
            target_dict=self._world.agents_data[self.id]['state'],
            event_recorder=..., # 从 World 或 Schedule 获取
            context_provider=..., # 从 World 或 Schedule 获取
            path=('agents', self.id, 'state')
        )
    
    # .properties 和 .reminders 也用同样的方式实现
```

### 3.2 工作流总结

1.  `Schedule` 在执行 `Node` 时，创建 `ContextStack` 和 `Event` 记录器。
2.  `Schedule` 从 `World` 中获取 `Agent` 实例（例如 `world.agent("alice")`）。
3.  `Schedule` 将 `Agent` 实例和 `event_recorder`、`context_provider` 等回调函数传递给 `Action`/`Behavior`。
4.  `Action`/`Behavior` 内部执行 `agent.state['key'] = value`。
5.  `Agent` 的 `@property` getter 被触发，返回一个配置好的 `DictProxy`。
6.  `DictProxy` 的 `__setitem__` 方法被触发，它调用回调函数记录事件，并修改 `World` 中的真实数据。
7.  整个过程对 `Action`/`Behavior` 的开发者完全透明。

---

这份设计方案详细阐述了一个健壮、透明且功能完备的状态代理系统，它是我们整个“统一状态架构”得以实现的技术基石。